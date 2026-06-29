import sys; sys.path.insert(0, '.')
from stock_data import get_stock_data, SYMBOL_MAP
from analysis import analyze, get_signal
import warnings; warnings.filterwarnings('ignore')

print('الراجحي in SYMBOL_MAP:', 'الراجحي' in SYMBOL_MAP)
print('الراجحي maps to:', SYMBOL_MAP.get('الراجحي'))

hist, info = get_stock_data('الراجحي', period='6mo', fetch_info=True)
if hist is None:
    print('hist is None!')
else:
    price = round(float(hist['Close'].iloc[-1]), 2)
    a = analyze(hist)
    s = get_signal(a)
    print(f'Price: {price}, Score: {a.get("total_score", 0)}, Verdict: {s.get("verdict", "?")}')
