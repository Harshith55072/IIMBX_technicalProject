// Syntax-check the inline <script> of static/index.html:  node scripts/check_js.js
const fs = require("fs");
const html = fs.readFileSync(__dirname + "/../static/index.html", "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("no <script> found"); process.exit(1); }
try { new Function(m[1]); console.log("JS syntax OK (" + m[1].length + " chars)"); }
catch (e) { console.error("JS syntax error:", e.message); process.exit(1); }
