// The phone page's VNC client: noVNC's RFB, as one classic script (window.NoVNC.RFB).
import RFB from "@novnc/novnc/lib/rfb.js";
window.NoVNC = { RFB: RFB.default || RFB, version: "1.6.0" };
