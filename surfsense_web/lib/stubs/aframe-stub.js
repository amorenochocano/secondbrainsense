// Stub que inicializa el global AFRAME mínimo para que aframe-extras no rompa.
// Solo se usa ForceGraph2D — los componentes VR/AR nunca se registran de verdad.
if (typeof window !== "undefined" && !window.AFRAME) {
  window.AFRAME = {
    registerComponent: function () {},
    registerSystem: function () {},
    registerGeometry: function () {},
    registerShader: function () {},
    registerPrimitive: function () {},
    utils: { coordinates: {}, device: {}, styleParser: {} },
    THREE: {},
    scenes: [],
    version: "0.0.0-stub",
  };
}

module.exports = (typeof window !== "undefined" && window.AFRAME) || {};
