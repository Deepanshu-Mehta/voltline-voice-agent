// Copies the Silero VAD model, its AudioWorklet and the ONNX Runtime WebAssembly files into
// public/vad so the browser can load them from this site. Runs before `dev` and `build`.
import fs from "node:fs";
import path from "node:path";

const root = path.dirname(new URL(import.meta.url).pathname);
const nm = path.join(root, "..", "node_modules");
const out = path.join(root, "..", "public", "vad");
fs.mkdirSync(out, { recursive: true });
for (const old of fs.readdirSync(out)) fs.unlinkSync(path.join(out, old));

const files = [
  [path.join(nm, "@ricky0123/vad-web/dist"), ["silero_vad_v5.onnx", "vad.worklet.bundle.min.js"]],
  // onnxruntime-web/wasm only needs the plain SIMD build, not the WebGPU or JSPI variants.
  [path.join(nm, "onnxruntime-web/dist"), ["ort-wasm-simd-threaded.wasm", "ort-wasm-simd-threaded.mjs"]],
];
let n = 0;
for (const [dir, names] of files) {
  for (const name of names) {
    fs.copyFileSync(path.join(dir, name), path.join(out, name));
    n++;
  }
}
console.log(`copied ${n} VAD assets to public/vad`);
