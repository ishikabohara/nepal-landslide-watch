import { ArrowUpRight, Database, ExternalLink } from 'lucide-react';
import { date, humanize, number, safeLink, time } from './format';
import type { Source } from './types';

function timestamp(value: unknown): string {
  if (typeof value !== 'string' || !value) return 'Not recorded';
  let input = value;
  if (input.includes('T') && !/(Z|[+-]\d{2}:?\d{2})$/.test(input)) input += 'Z';
  input = input.replace(/(\.\d{3})\d+/, '$1');
  if (Number.isNaN(new Date(input).getTime())) return value;
  return `${date(input)} · ${time(input)} NPT`;
}
const textValue = (value: unknown): string => Array.isArray(value) ? value.map(String).join(', ') : typeof value === 'boolean' ? value ? 'Yes' : 'No' : value == null ? 'Not recorded' : String(value);

export function SourceCard({ source, index }: { source: Source; index: number }) {
  const observationEnd = source.latest_observation_end ?? source.latest_observation_at ?? source.observed_through;
  const accessed = source.accessed_at ?? source.retrieved_at;
  const fields: [string, string][] = [];
  const technicalFields: [string, string][] = [];
  fields.push(['Retrieved', timestamp(accessed)]);
  const resolution = source.spatial_resolution ?? (source.resolution_degrees != null ? `${source.resolution_degrees}° grid` : null);
  if (resolution) fields.push(['Resolution', textValue(resolution)]);
  fields.push(['License', source.license ? textValue(source.license) : 'Not recorded']);
  if (observationEnd) technicalFields.push(['Latest observation', timestamp(observationEnd)]);
  if (source.cycle) technicalFields.push(['Forecast cycle', timestamp(source.cycle)]);
  if (source.valid_from && source.valid_to) technicalFields.push(['Forecast window', `${timestamp(source.valid_from)} → ${timestamp(source.valid_to)}`]);
  if (source.temporal_resolution) technicalFields.push(['Time resolution', textValue(source.temporal_resolution)]);
  if (source.coverage) technicalFields.push(['Coverage', textValue(source.coverage)]);
  if (typeof source.record_count === 'number') technicalFields.push(['Source records', number(source.record_count)]);
  if (source.version) technicalFields.push(['Product version', textValue(source.version)]);
  if (source.latency) technicalFields.push(['Published latency', textValue(source.latency)]);
  if (source.model_input != null) technicalFields.push(['Used by the model', textValue(source.model_input)]);
  if (source.access) technicalFields.push(['Access', textValue(source.access)]);
  if (source.attribution) technicalFields.push(['Attribution', textValue(source.attribution)]);
  const sourceUrl = safeLink(source.url), downloadUrl = safeLink(source.download_url);
  const licenseUrl = safeLink(source.license_url);
  return <article className="source-card">
    <div className="source-card-heading"><div className="source-icon"><Database size={20}/></div><div><h3>{String(source.name || source.title || `Data source ${index + 1}`)}</h3></div>{source.status != null && <span className={`source-status ${['available', 'ready', 'downloaded', 'cached'].includes(String(source.status)) ? 'ready' : ''}`}>{humanize(String(source.status))}</span>}</div>
    <div className="source-details">{fields.map(([label, value]) => <div key={label}><span>{label}</span><span>{value}</span></div>)}</div>
    <div className="source-links">{sourceUrl && <a className="source-link" href={sourceUrl} target="_blank" rel="noreferrer">Source <ArrowUpRight size={15}/></a>}{downloadUrl && downloadUrl !== sourceUrl && <a className="source-link" href={downloadUrl} target="_blank" rel="noreferrer">Download <ExternalLink size={13}/></a>}{licenseUrl && <a className="source-link" href={licenseUrl} target="_blank" rel="noreferrer">License <ExternalLink size={13}/></a>}</div>
    {(technicalFields.length > 0 || source.sha256 || source.snapshot_id) ? <details className="source-integrity"><summary>Technical details</summary><div className="source-details">{technicalFields.map(([label, value]) => <div key={label}><span>{label}</span><span>{value}</span></div>)}</div>{source.snapshot_id != null && <p><span>Snapshot</span><code>{textValue(source.snapshot_id)}</code></p>}{source.sha256 != null && <p><span>SHA-256</span><code>{textValue(source.sha256)}</code></p>}</details> : null}
  </article>;
}
