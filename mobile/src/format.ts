export const formatMoney = (value: string, currency: string) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value));

export const formatState = (state: string) =>
  state.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());

export const formatAuditTime = (value: string) => {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Recorded by QuoteHawk";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parsed);
};

export const humanizeEventType = (eventType: string) =>
  eventType.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
