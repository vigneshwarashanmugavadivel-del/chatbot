"""Validated catalog constraints and read-only numeric runtime fields."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

FilterValue = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
FilterValues = Annotated[list[FilterValue], Field(max_length=10)]
Amount = Annotated[float, Field(ge=0, le=1_000_000_000, allow_inf_nan=False)]
Measurement = Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)]
Region = Literal['AE', 'SA', 'QA', 'OM', 'BH']


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    categories: FilterValues = Field(default_factory=list, description='Product category phrases, e.g. sofa or dining chairs.')
    colors: FilterValues = Field(default_factory=list, description='Color phrases. beige also matches light beige.')
    materials: FilterValues = Field(default_factory=list, description='Material/finish phrases, e.g. leather, marble, solid wood.')
    sizes: FilterValues = Field(default_factory=list, description='Catalog size labels, e.g. small or large; not seat count.')
    product_types: FilterValues = Field(default_factory=list, description='Internal catalog types, e.g. simple, configurable, bundle; not categories.')
    brands: FilterValues = Field(default_factory=list)
    min_price: Amount | None = None
    max_price: Amount | None = None
    currency: Annotated[str, Field(pattern=r'^[A-Z]{3}$')] | None = Field(default=None, description='ISO currency. Defaults to selected storefront currency for price constraints. No exchange-rate conversion.')
    on_sale: bool | None = Field(default=None, description='true: valid positive sale below regular price; false: no discount.')
    min_length_cm: Measurement | None = None
    max_length_cm: Measurement | None = None
    min_width_cm: Measurement | None = None
    max_width_cm: Measurement | None = None
    min_height_cm: Measurement | None = None
    max_height_cm: Measurement | None = None

    @model_validator(mode='after')
    def ordered_ranges(self):
        for key in ['price', 'length_cm', 'width_cm', 'height_cm']:
            lower, upper = getattr(self, f'min_{key}'), getattr(self, f'max_{key}')
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f'min_{key} must not exceed max_{key}')
        return self


def category_phrases(value):
    """Accept common singular/plural category wording without matching descriptions."""
    value = value.lower()
    words = value.split()
    if not words:
        return [value]
    last = words[-1]
    if last.endswith('ies'):
        alternative = last[:-3] + 'y'
    elif last.endswith('s') and not last.endswith('ss'):
        alternative = last[:-1]
    elif last.endswith('ss'):
        return [value]
    else:
        alternative = last + 's'
    return [value, ' '.join(words[:-1] + [alternative])]


def build_filter_clauses(filters, fields, runtime, currency):
    """Translate validated preferences: OR within each list, AND across filters."""
    clauses = [{'range': {fields['stock']: {'gt': 0}}}]
    phrase_fields = {
        'categories': 'category', 'colors': 'color', 'materials': 'material',
        'sizes': 'size', 'brands': 'brand',
    }
    exact_fields = {'product_types': 'product_type'}
    for name, source in phrase_fields.items():
        values = getattr(filters, name)
        if name == 'categories':
            values = list(dict.fromkeys(phrase for value in values for phrase in category_phrases(value)))
        if values:
            clauses.append({'bool': {'should': [
                {'match_phrase': {fields[source] + '.text': value}} for value in values
            ], 'minimum_should_match': 1}})
    for name, source in exact_fields.items():
        values = getattr(filters, name)
        if values:
            clauses.append({'terms': {fields[source] + '.keyword': [value.lower() for value in values]}})
    for name in ('price', 'length_cm', 'width_cm', 'height_cm'):
        bounds = {operator: getattr(filters, prefix + name)
                  for prefix, operator in [('min_', 'gte'), ('max_', 'lte')]
                  if getattr(filters, prefix + name) is not None}
        if bounds:
            clauses.append({'range': {'chat_' + name: bounds}})
    if 'chat_currency' in runtime:
        clauses.append({'term': {'chat_currency': filters.currency or currency}})
    if filters.on_sale is not None:
        clauses.append({'term': {'chat_on_sale': filters.on_sale}})
    return clauses


# Parse only a number (comma thousands, dot decimal) plus an optional ISO code.
# Do not turn arbitrary/malformed strings, unknown prices, or currencies into zero.
PRICE_FUNCTIONS = r'''
def parse(def raw, String defaultCurrency) {
    if (raw == null) return null;
    String value = raw.toString().trim().toUpperCase();
    if (value.length() == 0) return null;
    String currency = defaultCurrency;
    if (value.length() >= 3) {
        String first = value.substring(0, 3);
        String last = value.substring(value.length() - 3);
        boolean firstCode = true; boolean lastCode = true;
        for (int i=0; i<3; i++) {
            int a = (int)first.charAt(i); int b = (int)last.charAt(i);
            if (a < 65 || a > 90) firstCode = false;
            if (b < 65 || b > 90) lastCode = false;
        }
        if (firstCode) {currency = first; value = value.substring(3).trim();}
        else if (lastCode) {currency = last; value = value.substring(0,value.length()-3).trim();}
    }
    if (value.length() == 0) return null;
    String numeric = ''; int dots=0; int commas=0; int group=0;
    boolean fractional=false;
    for (int i=0; i<value.length(); i++) {
        int ch = (int)value.charAt(i);
        if (ch >= 48 && ch <= 57) { numeric += value.substring(i,i+1); group++; }
        else if (ch == 44) {
            if (fractional || group == 0 || (commas == 0 && group > 3) || (commas > 0 && group != 3)) return null;
            commas++; group=0;
        } else if (ch == 46) {
            if (++dots > 1 || group == 0 || (commas > 0 && group != 3)) return null;
            fractional=true; numeric+='.'; group=0;
        } else return null;
    }
    if (group == 0 || (!fractional && commas > 0 && group != 3)) return null;
    try {
        double amount = Double.parseDouble(numeric);
        if (amount <= 0 || Double.isInfinite(amount) || Double.isNaN(amount)) return null;
        return ['amount':amount, 'currency':currency];
    } catch (Exception e) {return null;}
}
def effective(def regular, def sale) {
    if (sale != null && (regular == null || (sale.currency == regular.currency && sale.amount < regular.amount))) return sale;
    return regular;
}
'''

PRICE_SETUP = r'''
def data=params['_source'];
if (params.region != '') {
    data=data[params.region];
    if (!(data instanceof Map)) return;
}
def regular=parse(data[params.price],params.currency);
def sale=parse(data[params.sale_price],params.currency);
def chosen=effective(regular,sale);
'''

DIMENSION_SCRIPT = r'''
def data=params['_source'];
if (params.region != '') {
    data=data[params.region];
    if (!(data instanceof Map)) return;
}
def raw=data[params.field];
if (!(raw instanceof String)) return;
String value=raw.toLowerCase();
if (value.contains('/') || value.contains(' or ')) return;
String label=params.axis + ':';
int start=-1; int position=value.indexOf(label);
while (position>=0) {
    String prefix=value.substring(0,position).trim();
    if (!prefix.endsWith('seat') && !prefix.endsWith('arm') && !prefix.endsWith('back')) {
        if (start>=0) return;
        start=position;
    }
    position=value.indexOf(label,position+label.length());
}
if (start<0) return;
start+=label.length();
while (start<value.length() && Character.isWhitespace(value.charAt(start))) start++;
int end=start; int dots=0;
while (end<value.length()) {
    int ch=(int)value.charAt(end);
    if (ch>=48 && ch<=57) end++;
    else if (ch==46 && dots++==0) end++;
    else break;
}
if (end==start) return;
double amount;
try {amount=Double.parseDouble(value.substring(start,end));} catch (Exception e) {return;}
while (end<value.length() && Character.isWhitespace(value.charAt(end))) end++;
String unit=value.substring(end);
if (unit.startsWith('cm')) amount*=1;
else if (unit.startsWith('mm')) amount/=10;
else if (unit.startsWith('meters') || unit.startsWith('metres') || unit.startsWith('m ') || unit=='m') amount*=100;
else return;
if (amount>0 && !Double.isInfinite(amount) && !Double.isNaN(amount)) emit(amount);
'''


def runtime_fields(fields, filters, region, currency, sort):
    mappings = {}
    need_price = filters.min_price is not None or filters.max_price is not None or sort.startswith('price_')
    need_currency = need_price or filters.currency is not None
    params = {'price': fields['price'], 'sale_price': fields['sale_price'], 'region': region, 'currency': currency}
    for name, kind, emit, needed in [
        ('chat_price', 'double', 'if (chosen != null) emit((double)chosen.amount);', need_price),
        ('chat_currency', 'keyword', 'if (chosen != null) emit((String)chosen.currency);', need_currency),
        ('chat_on_sale', 'boolean', 'emit(regular != null && sale != null && regular.currency == sale.currency && sale.amount < regular.amount);', filters.on_sale is not None),
    ]:
        if needed:
            mappings[name] = {'type': kind, 'script': {'source': PRICE_FUNCTIONS + PRICE_SETUP + emit, 'params': params}}
    for axis in ['length', 'width', 'height']:
        if getattr(filters, f'min_{axis}_cm') is not None or getattr(filters, f'max_{axis}_cm') is not None:
            mappings[f'chat_{axis}_cm'] = {'type': 'double', 'script': {'source': DIMENSION_SCRIPT,
                'params': {'field': fields['dimension'], 'axis': axis, 'region': region}}}
    return mappings


def strict_schema(schema):
    """Make all properties required for OpenAI strict tools; defaults remain server-side."""
    if isinstance(schema, dict):
        schema.pop('default', None)
        if schema.get('type') == 'object':
            schema['additionalProperties'] = False
            schema['required'] = list(schema.get('properties', {}))
        for value in schema.values():
            strict_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            strict_schema(value)
    return schema
