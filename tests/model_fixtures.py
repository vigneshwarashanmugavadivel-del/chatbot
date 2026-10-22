import json

QUESTIONS = ['How do these compare?', 'What goes with these?', 'Can I see other options?', 'Which is easiest to clean?']


def answer(reply):
    return json.dumps({'reply': reply, 'follow_ups': QUESTIONS})
