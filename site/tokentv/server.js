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
  if (pathname === "/api/visits") {
    const urlObj = new URL(req.url, "http://localhost");
    const isHit = urlObj.searchParams.get("hit") !== "0";
    const VISITS_FILE = path.join(ROOT, ".visits.json");
    let count = 1;
    try {
      if (fs.existsSync(VISITS_FILE)) {
        count = JSON.parse(fs.readFileSync(VISITS_FILE, "utf8")).count || 1;
      }
    } catch {}
    if (isHit && req.method === "GET") {
      count += 1;
      try {
        fs.writeFileSync(VISITS_FILE, JSON.stringify({ count }), "utf8");
      } catch {}
    }
    return send(res, 200, JSON.stringify({ value: count }), { "Content-Type": TYPES[".json"], "Cache-Control": "no-store" });
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

    if (range && stat.size > 0) {
      const parts = range.replace(/bytes=/, "").split("-");
      const start = parseInt(parts[0], 10);
      const end = parts[1] ? parseInt(parts[1], 10) : stat.size - 1;

      if (isNaN(start) || isNaN(end) || start > end || end >= stat.size) {
        res.writeHead(416, {
          "Content-Range": `bytes */${stat.size}`,
          "Content-Type": type,
        });
        return res.end();
      }

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
