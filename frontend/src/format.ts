const nf = (d: number) => new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });

export function price(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  const d = a >= 1000 ? 0 : a >= 1 ? 2 : a >= 0.01 ? 4 : 8;
  return nf(d).format(v);
}

/** Decimals needed to tell order book levels apart (USDT books tick in cents; "81,734" x4 looked fake). */
export function bookDecimals(prices: number[]): number {
  let d = 0;
  for (const p of prices) {
    const s = String(p);
    const i = s.indexOf(".");
    if (i >= 0 && !s.includes("e")) d = Math.max(d, s.length - i - 1);
  }
  return Math.min(d, 8);
}

export function bookPrice(v: number | null | undefined, decimals: number): string {
  if (v == null || !isFinite(v)) return "—";
  return new Intl.NumberFormat("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(v);
}

export function compact(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  if (Math.abs(v) < 1) return new Intl.NumberFormat("en-US", { maximumSignificantDigits: 4 }).format(v);
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v);
}

export function pct(v: number | null | undefined, digits = 2): string {
  if (v == null || !isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");

export function splitSymbol(s: string): [string, string] {
  for (const q of ["USDT", "TMN"]) if (s.endsWith(q)) return [s.slice(0, -q.length), q];
  return [s, ""];
}

export const quoteFa: Record<string, string> = { USDT: "تتر", TMN: "تومان" };

export const toolLabels: Record<string, string> = {
  list_markets: "لیست بازارها",
  get_ticker: "قیمت لحظه‌ای",
  top_movers: "پربازده‌ترین بازارها",
  order_book: "عمق بازار",
  recent_trades: "آخرین معاملات",
  get_candles: "کندل‌ها",
  technical_analysis: "تحلیل تکنیکال",
  fear_greed_index: "شاخص ترس و طمع",
  market_risk: "شاخص ریسک",
  global_coin_stats: "آمار جهانی",
  crypto_news: "اخبار رمزارز",
  web_search: "جستجوی اینترنت",
  read_webpage: "خواندن صفحه",
  spot_balances: "دارایی اسپات",
  spot_open_orders: "سفارش‌های باز",
  spot_trade_history: "تاریخچه معاملات",
  futures_account: "حساب فیوچرز",
  futures_history: "تاریخچه فیوچرز",
  futures_markets: "بازارهای اهرمی",
  spot_place_order: "ثبت سفارش اسپات",
  spot_cancel_order: "لغو سفارش",
  spot_close_asset: "فروش کامل دارایی",
  futures_open_position: "باز کردن پوزیشن",
  futures_close_position: "بستن پوزیشن",
  futures_set_tp_sl: "تنظیم حد سود/ضرر",
  futures_cancel_order: "لغو سفارش فیوچرز",
  show_comparison_chart: "نمودار مقایسه",
  show_market_heatmap: "نقشه حرارتی",
  show_price_chart: "نمودار تحلیلی",
  show_trade_ticket: "برگه سفارش",
  show_position_planner: "برنامه‌ریز پوزیشن",
  render_dashboard: "گزارش",
  create_price_alert: "ساخت هشدار قیمت",
  list_price_alerts: "هشدارها",
  cancel_price_alert: "لغو هشدار",
};

export function fgColor(v: number): string {
  if (v < 25) return "var(--down)";
  if (v < 45) return "#F59E0B";
  if (v <= 55) return "var(--muted)";
  if (v <= 75) return "#22B07D";
  return "var(--up)";
}

export const fgFa: Record<string, string> = {
  "Extreme Fear": "ترس شدید",
  Fear: "ترس",
  Neutral: "خنثی",
  Greed: "طمع",
  "Extreme Greed": "طمع شدید",
};
