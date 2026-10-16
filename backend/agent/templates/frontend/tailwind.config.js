// Builds vendor/app.css for the generated web app (run ./build_css.sh after changing app.js).
// The accent colour is chosen at runtime (ui.json), so its classes are listed explicitly.
module.exports = {
  content: [__dirname + "/app.js", __dirname + "/index.html"],
  safelist: [
    {
      pattern: /^(bg|text|border|ring)-(indigo|violet|emerald|sky|rose|amber|teal|slate)-(50|600|700)$/,
      variants: ["hover"],
    },
  ],
  theme: { extend: { fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"] } } },
};
