// Builds web/vendor/novnc-rfb.js: noVNC 1.6.0's RFB client (npm @novnc/novnc, MPL-2.0) as one classic script.
// One change, made here and said in the file's banner: lib/util/browser.js decides at load whether this browser can
// decode H.264 with a top-level `await`, which a classic script cannot hold; the check is started instead and its
// answer published when it comes (rfb.js reads the flag when it offers encodings, so a connection made later sees it).
import * as esbuild from "esbuild";
import fs from "fs";
const AWAIT = "exports.supportsWebCodecsH264Decode = supportsWebCodecsH264Decode = await _checkWebCodecsH264DecodeSupport();";
const LATER = "_checkWebCodecsH264DecodeSupport().then(function (ok) { exports.supportsWebCodecsH264Decode = supportsWebCodecsH264Decode = ok; }, function () {});";
const patch = {
  name: "no-top-level-await",
  setup(b) {
    b.onLoad({ filter: /@novnc[\\/]novnc[\\/]lib[\\/]util[\\/]browser\.js$/ }, async (args) => {
      const src = await fs.promises.readFile(args.path, "utf8");
      if (src.split(AWAIT).length !== 2) throw new Error("browser.js is not the 1.6.0 file this build patches");
      return { contents: src.replace(AWAIT, LATER), loader: "js" };
    });
  },
};
await esbuild.build({
  entryPoints: ["entry.js"], bundle: true, format: "iife", target: "chrome110", legalComments: "inline",
  outfile: process.argv[2] || "novnc-rfb.js", plugins: [patch], logLevel: "warning",
  banner: { js: "/* noVNC 1.6.0 (https://github.com/novnc/noVNC): Copyright (C) The noVNC authors; licensed under the " +
    "Mozilla Public License 2.0 (noVNC-LICENSE.txt beside this file).  Built for the open77_phone page from npm " +
    "@novnc/novnc@1.6.0 by build-novnc.mjs: one classic script exposing window.NoVNC.RFB.  One change: " +
    "lib/util/browser.js's top-level `await` of its H.264 check is started instead, and its answer published when it " +
    "comes. */" },
});
