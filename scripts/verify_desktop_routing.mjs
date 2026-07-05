import { isDesktopLoopbackApiUrl } from "../src/lib/desktop-routing.ts";

const positive = [
  "http://127.0.0.1:8757/api",
  "http://127.0.0.1:8757/api/app/bootstrap",
  "http://localhost:8757/api/app/bootstrap",
  "http://[::1]:8757/api/app/bootstrap",
  "http://[::1]:8757/api?x=1",
];

const negative = [
  "http://127.0.0.1:8757/mcp",
  "http://127.0.0.1:8757/runtime-artifacts/example.png",
  "http://127.0.0.1:8758/api/app/bootstrap",
  "https://127.0.0.1:8757/api/app/bootstrap",
  "http://192.168.1.2:8757/api/app/bootstrap",
  "not a url",
];

const failures = [];

for (const url of positive) {
  if (!isDesktopLoopbackApiUrl(url)) {
    failures.push(`expected blocked: ${url}`);
  }
}

for (const url of negative) {
  if (isDesktopLoopbackApiUrl(url)) {
    failures.push(`expected allowed: ${url}`);
  }
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log(`desktop routing checks passed (${positive.length + negative.length})`);
