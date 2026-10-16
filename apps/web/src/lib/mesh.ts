/** Decoding of base64 little-endian arrays produced by the kernel tessellator. */
export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function f32(b64: string): Float32Array {
  const bytes = b64ToBytes(b64);
  return new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}

export function u32(b64: string): Uint32Array {
  const bytes = b64ToBytes(b64);
  return new Uint32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}

export function fmt(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a < 1e-3 || a >= 1e7) return v.toExponential(digits - 1);
  return parseFloat(v.toPrecision(digits)).toString();
}

export const LENGTH_UNITS: Record<string, number> = { mm: 1, cm: 10, m: 1000, in: 25.4 };

/** Convert a value entered in display units to an unambiguous stored expression. */
export function toStoredExpr(input: string, unit: string): string {
  const s = input.trim();
  if (/^-?\d+(\.\d+)?([eE][-+]?\d+)?$/.test(s) && unit !== "mm") return `${s} ${unit}`;
  return s;
}

export function uid(prefix: string, taken: Set<string>): string {
  let i = 1;
  while (taken.has(`${prefix}${i}`)) i++;
  return `${prefix}${i}`;
}
