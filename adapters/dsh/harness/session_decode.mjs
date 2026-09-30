/** Bounded decoder for the concatenated Zstandard frames written by DSH. */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as zlib from "node:zlib";

const MAX_BYTES = 1024 * 1024;
const MAX_FRAMES = 4096;
// DSH's streaming frames declare a 2 MiB window even for small JSONL batches.
const MAX_WINDOW_BYTES = 8 * 1024 * 1024;

// RFC 8878 section 3.1.1: check the physical frame before asking zlib to
// decode. Node can return partial data for a torn frame without throwing.
// This DSH reader refuses skippable frames and external dictionaries.
function frameEnd(bytes, start) {
  let cursor = start;
  const take = (size) => {
    const at = cursor;
    cursor += size;
    if (cursor > bytes.length) throw new Error("torn frame");
    return at;
  };
  if (bytes.readUInt32LE(take(4)) !== 0xfd2fb528) throw new Error("frame magic");
  const flags = bytes[take(1)];
  if (flags & 0x18) throw new Error("reserved frame flags");
  const single = Boolean(flags & 0x20);
  if (!single) {
    const window = bytes[take(1)];
    const base = 2 ** (10 + (window >>> 3));
    if (base + (base / 8) * (window & 7) > MAX_WINDOW_BYTES) throw new Error("window limit");
  }
  const dictionarySize = [0, 1, 2, 4][flags & 3];
  const dictionaryAt = take(dictionarySize);
  if (dictionarySize && bytes.readUIntLE(dictionaryAt, dictionarySize) !== 0) {
    throw new Error("dictionary unsupported");
  }
  const sizeFlag = flags >>> 6;
  const sizeBytes = sizeFlag === 0 ? (single ? 1 : 0) : [0, 2, 4, 8][sizeFlag];
  const sizeAt = take(sizeBytes);
  if (sizeBytes) {
    const size = sizeBytes === 8 ? bytes.readBigUInt64LE(sizeAt)
      : BigInt(bytes.readUIntLE(sizeAt, sizeBytes) + (sizeBytes === 2 ? 256 : 0));
    if (size > BigInt(MAX_BYTES)) throw new Error("content size limit");
  }
  let last = false;
  for (let blocks = 0; !last; blocks += 1) {
    if (blocks >= MAX_FRAMES) throw new Error("block limit");
    const header = bytes.readUIntLE(take(3), 3);
    const kind = (header >>> 1) & 3;
    const size = header >>> 3;
    if (kind === 3 || size > 128 * 1024) throw new Error("invalid block");
    take(kind === 1 ? 1 : size);
    last = Boolean(header & 1);
  }
  if (flags & 4) take(4);
  return cursor;
}

export function decodeSession(bytes) {
  if (bytes.length === 0 || bytes.length > MAX_BYTES) throw new Error("input limit");
  const chunks = [];
  let offset = 0;
  let total = 0;
  while (offset < bytes.length) {
    if (chunks.length >= MAX_FRAMES) throw new Error("frame limit");
    const end = frameEnd(bytes, offset);
    const decoded = zlib.zstdDecompressSync(bytes.subarray(offset, end), {
      info: true,
      maxOutputLength: Math.max(1, MAX_BYTES - total),
    });
    const consumed = decoded.engine.bytesWritten;
    if (consumed !== end - offset) {
      throw new Error("invalid frame progress");
    }
    total += decoded.buffer.length;
    if (total > MAX_BYTES) throw new Error("output limit");
    chunks.push(decoded.buffer);
    offset = end;
  }
  return Buffer.concat(chunks, total);
}

function main() {
  try {
    if (process.argv[2] === "--probe" && process.argv.length === 3) {
      const supported = typeof zlib.zstdDecompressSync === "function";
      process.stdout.write(JSON.stringify({ version: process.version, zstd_supported: supported }) + "\n");
      return supported ? 0 : 1;
    }
    if (process.argv.length !== 3) throw new Error("arguments");
    const fd = fs.openSync(process.argv[2], fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    let bytes;
    try {
      if (!fs.fstatSync(fd).isFile()) throw new Error("input type");
      const buffer = Buffer.alloc(MAX_BYTES + 1);
      let count = 0;
      while (count < buffer.length) {
        const size = fs.readSync(fd, buffer, count, buffer.length - count, null);
        if (size === 0) break;
        count += size;
      }
      bytes = buffer.subarray(0, count);
    } finally {
      fs.closeSync(fd);
    }
    // Emit only after every frame succeeds, including a possible torn tail.
    process.stdout.write(decodeSession(bytes));
    return 0;
  } catch {
    process.stderr.write("DSH native session decode refused; raw input is not printed\n");
    return 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
