/* Static file server for the TokenTV project page.
   No dependencies: Railway's Node builder runs `npm start`, which runs this.
   Binds 0.0.0.0 on $PORT, serves only files beneath this directory, and refuses anything else. */
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname);
const PORT = Number(process.env.PORT) || 3000;
const PRIVATE = new Set(["server.js", "package.json", "package-lock.json", "README.md"]);

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".txt": "text/plain; charset=utf-8",
};

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "X-Content-Type-Options": "nosniff", ...headers });
  res.end(body);
}

const server = http.createServer((req, res) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return send(res, 405, "Method Not Allowed", { "Content-Type": TYPES[".txt"], Allow: "GET, HEAD" });
  }

  let pathname;
  try {
    pathname = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
  } catch {
    return send(res, 400, "Bad Request", { "Content-Type": TYPES[".txt"] });
  }
  if (pathname.endsWith("/")) pathname += "index.html";

  // Resolve, then confirm the result is still inside ROOT. This is what stops `..` traversal,
  // including encoded forms, rather than trying to pattern-match the request.
  const target = path.resolve(ROOT, "." + pathname);
  if (target !== ROOT && !target.startsWith(ROOT + path.sep)) {
    return send(res, 403, "Forbidden", { "Content-Type": TYPES[".txt"] });
  }

  // The page is the product; its own source is not. Dotfiles stay private too.
  const name = path.basename(target);
  if (PRIVATE.has(name) || name.startsWith(".")) {
    return send(res, 404, "Not Found", { "Content-Type": TYPES[".txt"] });
  }

  fs.stat(target, (err, stat) => {
    if (err || !stat.isFile()) {
      return send(res, 404, "Not Found", { "Content-Type": TYPES[".txt"] });
    }
    const type = TYPES[path.extname(target).toLowerCase()] || "application/octet-stream";
    const immutable = target.includes(`${path.sep}assets${path.sep}`);
    const range = req.headers.range;

    if (range && stat.size > 0 && range.startsWith("bytes=")) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
      if (match) {
        let start = match[1] ? parseInt(match[1], 10) : null;
        let end = match[2] ? parseInt(match[2], 10) : null;

        if (start === null && end !== null) {
          // Suffix range: bytes=-N
          start = Math.max(0, stat.size - end);
          end = stat.size - 1;
        } else if (start !== null) {
          if (end === null || end >= stat.size) {
            end = stat.size - 1;
          }
        }

        if (start !== null && end !== null && start <= end && start < stat.size) {
          const chunksize = end - start + 1;
          const headers = {
            "Content-Range": `bytes ${start}-${end}/${stat.size}`,
            "Accept-Ranges": "bytes",
            "Content-Length": chunksize,
            "Content-Type": type,
            "Cache-Control": immutable ? "public, max-age=3600" : "public, max-age=300",
          };

          if (req.method === "HEAD") return send(res, 206, null, headers);
          res.writeHead(206, { "X-Content-Type-Options": "nosniff", ...headers });
          fs.createReadStream(target, { start, end }).pipe(res);
          return;
        }

        res.writeHead(416, {
          "Content-Range": `bytes */${stat.size}`,
          "Content-Type": type,
        });
        return res.end();
      }
    }

    const headers = {
      "Content-Type": type,
      "Content-Length": stat.size,
      "Accept-Ranges": "bytes",
      "Cache-Control": immutable ? "public, max-age=3600" : "public, max-age=300",
    };
    if (req.method === "HEAD") return send(res, 200, null, headers);
    res.writeHead(200, { "X-Content-Type-Options": "nosniff", ...headers });
    fs.createReadStream(target).pipe(res);
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`TokenTV page listening on 0.0.0.0:${PORT}`);
});

// Exit 0 on Railway's stop signal so redeploy handovers aren't logged as crashes.
// Closes idle keep-alive sockets immediately and sets a 1s fallback so lingering connections
// never hold the process past Railway's container shutdown window.
for (const sig of ["SIGTERM", "SIGINT"]) {
  process.on(sig, () => {
    setTimeout(() => process.exit(0), 1000).unref();
    if (typeof server.closeIdleConnections === "function") {
      server.closeIdleConnections();
    }
    server.close(() => process.exit(0));
  });
}
