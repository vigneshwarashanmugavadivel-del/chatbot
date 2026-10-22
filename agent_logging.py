"""Correlated JSONL and readable logs for agent steps and Elasticsearch requests."""

import json
import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
LOG_TIMEZONE = ZoneInfo('Asia/Kolkata')
ACTIVE_TRACE = ContextVar('agent_trace', default=None)
SECRET_KEYS = re.compile(r'(^|_)(api_key|password|authorization|secret|access_token|refresh_token)($|_)', re.I)


def sanitize(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, dict):
        if value.get('type') == 'reasoning':
            return {'type': 'reasoning', 'content': '[OMITTED]'}
        return {str(key): '[REDACTED]' if SECRET_KEYS.search(str(key)) else
                '[OMITTED]' if str(key) == 'encrypted_content' else sanitize(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        # Arguments and tool outputs arrive as JSON strings. Parse them before
        # redacting nested fields so secrets cannot hide inside serialized JSON.
        if value.lstrip().startswith(('{', '[')):
            try:
                return sanitize(json.loads(value))
            except (ValueError, TypeError):
                pass
        for name in ('OPENAI_API_KEY', 'ES_API_KEY', 'ES_PASSWORD'):
            secret = os.getenv(name)
            if secret:
                value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(https?://)[^\s/@:]+:[^\s/@]+@', r'\1[REDACTED]@', value)
        value = re.sub(r'(?i)(authorization\s*[:=]\s*)(?:(?:Bearer|ApiKey|Basic)\s+)?[^\s,;]+', r'\1[REDACTED]', value)
        value = re.sub(r'(?i)((?:api[_-]?key|password|access_token|refresh_token)\s*[:=]\s*)[^\s,;]+', r'\1[REDACTED]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{16,}', '[REDACTED]', value)
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if hasattr(value, '__dict__'):
        return sanitize(vars(value))
    return str(value)


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream


def format_readable(record):
    """Summarize a sanitized event; full payloads stay in JSONL."""
    event = record.get('event', 'event')
    if event in {'ui.delta', 'ui.event', 'turn.closed'}:
        return None

    def short(value, limit=240):
        text = ' '.join(str(value).split())
        return text if len(text) <= limit else text[:limit - 1] + '…'

    details = []
    if event == 'turn.started':
        messages = record.get('messages', [])
        user = next((message.get('content', '') for message in reversed(messages)
                     if isinstance(message, dict) and message.get('role') == 'user'), '')
        details.append('User: ' + short(user))
    elif event == 'turn.completed':
        result = record.get('result', {})
        details.append('Reply: ' + short(result.get('reply', record.get('reply', '')), 400))
    elif event == 'tool.completed':
        details.append(str(record.get('tool', 'tool')))
        result = record.get('result', {})
        if isinstance(result, dict) and isinstance(result.get('products'), list):
            details.append(f"{len(result['products'])} products")
        details.append('OK' if record.get('success', True) else 'FAILED')
    elif event == 'tool.started':
        details.append(str(record.get('tool', 'tool')))
    elif event == 'elasticsearch.response':
        details.append(f"HTTP {record.get('status_code', '?')}")
    elif event == 'model.completed':
        usage = record.get('usage') or {}
        if isinstance(usage, dict) and usage.get('total_tokens') is not None:
            details.append(f"{usage['total_tokens']} tokens")
    if record.get('round') is not None:
        details.append(f"round {record['round']}")
    if record.get('duration_ms') is not None:
        details.append(f"{record['duration_ms']:.0f} ms")
    if record.get('error') or record.get('error_type'):
        details.append(short(record.get('error') or record['error_type']))
    label = str(event).replace('.', ' / ').replace('_', ' ').title()
    timestamp = record.get('timestamp', '?')
    try:
        timestamp = datetime.fromisoformat(timestamp).isoformat(sep=' ', timespec='seconds')
    except (ValueError, TypeError):
        pass
    prefix = (f"{timestamp} [{record.get('trace_id', '?')}] "
              f"Step {record.get('step', '?')} | {label}")
    summary = prefix + (' | ' + ' | '.join(details) if details else '')
    if event == 'model.completed':
        outputs = []
        output_text = record.get('output_text')
        for item in record.get('output', []):
            if not isinstance(item, dict) or item.get('type') == 'reasoning':
                continue
            if item.get('type') == 'function_call':
                outputs.append({'tool': item.get('name'), 'call_id': item.get('call_id'),
                                'arguments': item.get('arguments')})
            elif item.get('type') != 'message' or not output_text:
                outputs.append(item)
        if output_text:
            outputs.append(output_text)
        # Keep the generated output complete (including structured follow-ups),
        # but compact. JSON escaping keeps multiline output on a single line.
        for output in outputs:
            summary += '\n  LLM output: ' + json.dumps(output, ensure_ascii=False, separators=(',', ':'))
        if not outputs:
            summary += '\n  LLM output: (empty)'
    return summary


class TraceWriter:
    def __init__(self, path, max_bytes=10 * 1024 * 1024, backups=5):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = PrivateRotatingHandler(path, maxBytes=max_bytes, backupCount=backups, encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger = logging.Logger(f'agent.trace.{uuid4().hex}', level=logging.INFO)
        self.logger.addHandler(self.handler)
        self.logger.propagate = False
        # Derive the companion path from any configured filename, without ever
        # colliding with the structured log when that filename ends in .log.
        self.readable_path = path.with_suffix('.log')
        if self.readable_path == path:
            self.readable_path = path.with_name(path.name + '.readable.log')
        backfill = not self.readable_path.exists()
        self.readable_handler = PrivateRotatingHandler(
            self.readable_path, maxBytes=max_bytes, backupCount=backups, encoding='utf-8')
        self.readable_handler.setFormatter(logging.Formatter('%(message)s'))
        self.readable_logger = logging.Logger(f'agent.readable.{uuid4().hex}', level=logging.INFO)
        self.readable_logger.addHandler(self.readable_handler)
        self.readable_logger.propagate = False
        # On first use, make existing events in the current JSONL file readable
        # too. Later starts append, so historical events are not duplicated.
        if backfill:
            with path.open(encoding='utf-8') as existing:
                for line in existing:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue  # A partially written final line is not an event.
                    if isinstance(record, dict):
                        self.write_readable(sanitize(record))

    def write(self, record):
        safe_record = sanitize(record)
        self.logger.info(json.dumps(safe_record, ensure_ascii=True))
        self.write_readable(safe_record)

    def write_readable(self, record):
        summary = format_readable(record)
        if summary is not None:
            self.readable_logger.info(summary)

    def close(self):
        self.handler.close()
        self.readable_handler.close()


@lru_cache(maxsize=8)
def configured_writer(path, max_bytes, backups):
    return TraceWriter(path, max_bytes, backups)


def get_writer():
    path = Path(os.getenv('AGENT_LOG_PATH', 'logs/agent.jsonl'))
    if not path.is_absolute():
        path = BASE_DIR / path
    return configured_writer(str(path), int(os.getenv('AGENT_LOG_MAX_BYTES', str(10 * 1024 * 1024))),
                             int(os.getenv('AGENT_LOG_BACKUP_COUNT', '5')))


class AgentTrace:
    def __init__(self, writer=None):
        self.trace_id = uuid4().hex
        self.writer = writer or get_writer()
        self.started = perf_counter()
        self.sequence = 0

    def emit(self, event, **data):
        self.sequence += 1
        self.writer.write({
            'timestamp': datetime.now(LOG_TIMEZONE).isoformat(),
            'trace_id': self.trace_id, 'step': self.sequence, 'event': event,
            'elapsed_ms': round((perf_counter() - self.started) * 1000, 2), **data,
        })

    @contextmanager
    def activate(self, **context):
        token = ACTIVE_TRACE.set((self, context))
        try:
            yield
        finally:
            ACTIVE_TRACE.reset(token)


def log_active(event, **data):
    active = ACTIVE_TRACE.get()
    if active:
        trace, context = active
        trace.emit(event, **{**context, **data})


async def log_es_request(request):
    if not ACTIVE_TRACE.get():
        return
    request.extensions['agent_request_id'] = uuid4().hex
    request.extensions['agent_started'] = perf_counter()
    body = request.content.decode('utf-8', errors='replace')
    try:
        body = [json.loads(line) for line in body.splitlines() if line.strip()]
    except ValueError:
        pass
    log_active('elasticsearch.request', request_id=request.extensions['agent_request_id'],
               method=request.method, url=str(request.url), body=body)


async def log_es_response(response):
    if not ACTIVE_TRACE.get():
        return
    await response.aread()
    try:
        body = response.json()
    except ValueError:
        body = response.text
    started = response.request.extensions.get('agent_started', perf_counter())
    log_active('elasticsearch.response', request_id=response.request.extensions.get('agent_request_id'),
               status_code=response.status_code, duration_ms=round((perf_counter() - started) * 1000, 2), body=body)
