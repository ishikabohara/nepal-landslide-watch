import type { RiskLevel } from './types';

export const RISK_LEVELS: RiskLevel[] = ['low', 'moderate', 'high', 'very_high'];
export const RISK_COLORS: Record<RiskLevel, string> = { low: '#729bb5', moderate: '#f3cc68', high: '#e58b53', very_high: '#b94048', unavailable: '#a7acac' };
export const RISK_LABELS: Record<RiskLevel, string> = { low: 'Low', moderate: 'Moderate', high: 'High', very_high: 'Very high', unavailable: 'Unavailable' };
export const number = (value: number | null | undefined, digits = 0): string => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: digits });
export const humanize = (value: string): string => value.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
export function date(value: string | null | undefined, options: Intl.DateTimeFormatOptions = {}): string {
  if (!value || Number.isNaN(new Date(value).getTime())) return 'Unavailable';
  return new Date(value).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kathmandu', ...options });
}
export function time(value: string | null | undefined): string {
  if (!value || Number.isNaN(new Date(value).getTime())) return 'Unavailable';
  return new Date(value).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kathmandu' });
}
export function safeLink(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; }
}

export function nepalDay(value: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'Asia/Kathmandu' }).formatToParts(new Date(value));
  const get = (type: string) => parts.find(part => part.type === type)?.value || '';
  return `${get('year')}-${get('month')}-${get('day')}`;
}
