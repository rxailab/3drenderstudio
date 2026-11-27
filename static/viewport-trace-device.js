const nvidia = /\b(?:nvidia|geforce|quadro)\b|\b(?:0x|ven_)10de\b/i;
const otherGpu = /\b(?:intel|amd|radeon|apple|adreno|mali|powervr|qualcomm)\b|\b(?:0x|ven_)(?:8086|1002|1022)\b/i;
const software = /swiftshader|llvmpipe|softpipe|software|basic render|lavapipe|microsoft warp/i;

// The actual integration context can be different from a foreground WebGL or
// WebGPU adapter on hybrid laptops. An ambiguous or masked identity does not
// establish that the requested NVIDIA GPU was selected.
export function traceDevice(name, vendor) {
  const identity = [name,vendor].filter(Boolean).join(' ');
  const adapterName = name || vendor || '图形驱动未公开型号';
  if (software.test(identity)) return {adapterName,adapterType:'software'};
  if (nvidia.test(identity) && !otherGpu.test(identity)) return {adapterName,adapterType:'hardware'};
  return {adapterName,adapterType:'unknown'};
}
