import test from 'node:test';
import assert from 'node:assert/strict';
import {traceDevice} from '../static/viewport-trace-device.js';

test('recognizes a matching unmasked NVIDIA graphics context',()=>{
  const actual='ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Laptop GPU (0x0000249D) Direct3D11 vs_5_0 ps_5_0, D3D11)';
  assert.deepEqual(traceDevice(actual,'Google Inc. (NVIDIA)'),{adapterType:'hardware',adapterName:actual});
  assert.equal(traceDevice('Quadro P4000','').adapterType,'hardware');
  assert.equal(traceDevice('Vulkan (vendor: 0x10de)','').adapterType,'hardware');
});

test('software, masked and mixed adapters cannot start NVIDIA path tracing',()=>{
  assert.equal(traceDevice('ANGLE (Google, Vulkan SwiftShader Device)','NVIDIA').adapterType,'software');
  assert.equal(traceDevice('Microsoft Basic Render Driver','').adapterType,'software');
  for (const [name,vendor] of [
    ['ANGLE (Intel, Intel(R) Iris Xe Graphics D3D11)','Google Inc. (NVIDIA)'],
    ['ANGLE (NVIDIA, AMD Radeon Graphics, D3D11)','Google Inc.'],
    ['WebKit WebGL','WebKit'],['',''],['Adreno (TM) 740','Qualcomm'],
  ]) assert.equal(traceDevice(name,vendor).adapterType,'unknown');
});
