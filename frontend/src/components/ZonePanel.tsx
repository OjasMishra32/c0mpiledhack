import type { ObservedObject, Zone } from '../types/hive';
import { Rule } from './primitives';

function objectLabel(obj: ObservedObject): string {
  return obj.role ?? obj.semantic_label ?? `${obj.descriptor.color_name} ${obj.descriptor.shape_hint} object`;
}

const ZONE_STATUS_GLYPH: Record<Zone['status'], string> = {
  unknown: '○', pending: '◐', active: '◐', satisfied: '✓', blocked: '✕',
};

function ZoneRow({ zone, objects }: { zone: Zone; objects: ObservedObject[] }) {
  const occupants = zone.occupancy
    .map((id) => objects.find((o) => o.id === id))
    .filter((o): o is ObservedObject => Boolean(o));

  return (
    <div className="flex items-center justify-between px-4 py-2">
      <span className="text-[13px] font-medium text-fg-0">{zone.label.toUpperCase()}</span>
      <div className="flex items-center gap-3">
        <div className="flex gap-1">
          {occupants.map((o) => (
            <span
              key={o.id}
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: o.descriptor.color_hex }}
            />
          ))}
        </div>
        <span
          className={zone.status === 'blocked' ? 'text-crit' : zone.status === 'satisfied' ? 'text-ok' : 'text-fg-2'}
        >
          {ZONE_STATUS_GLYPH[zone.status]}
        </span>
      </div>
    </div>
  );
}

interface ZonePanelProps {
  objects: ObservedObject[];
  zones: Zone[];
  onScan?: () => void;
}

export function ZonePanel({ objects, zones, onScan }: ZonePanelProps) {
  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between px-4 pb-2 pt-3">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-fg-2">Scene</span>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-fg-2">
            {objects.length} OBJECTS · {zones.length} ZONES
          </span>
          <button
            onClick={onScan}
            className="rounded-sm border border-line-strong px-2 py-0.5 text-[11px] uppercase tracking-[0.1em] text-fg-1 transition-colors duration-200 ease-hive hover:border-info hover:text-info"
          >
            Scan
          </button>
        </div>
      </div>

      {objects.map((o) => (
        <div key={o.id} className="flex items-center justify-between px-4 py-1.5">
          <div className="flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: o.descriptor.color_hex }} />
            <span className="text-[13px] text-fg-1">{objectLabel(o)}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-fg-2">{o.zone}</span>
            <span className="font-mono text-[11px] text-fg-2">{o.confidence.toFixed(2)}</span>
          </div>
        </div>
      ))}

      <Rule className="my-2" />

      {zones.map((z) => (
        <ZoneRow key={z.id} zone={z} objects={objects} />
      ))}
    </div>
  );
}
