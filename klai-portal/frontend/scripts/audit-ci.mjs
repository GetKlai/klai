import { spawnSync } from "node:child_process";
import fs from "node:fs";

const severityRank = new Map([
  ["info", 0],
  ["low", 1],
  ["moderate", 2],
  ["high", 3],
  ["critical", 4],
]);

const tanstackAdvisoryUrl = "https://github.com/advisories/GHSA-rmmr-r34h-pfm5";
const tanstackCompromiseAdvisory =
  "https://github.com/TanStack/router/security/advisories/GHSA-g7cv-rxg3-hmpx";
const tanstackPayloadRef = "github:tanstack/router#79ac49eedf774dd4b0cfa308722bc463cfe5885c";
const tanstackSetupPackage = "@tanstack/setup";

const affectedTanstackVersions = new Map([
  ["@tanstack/history", new Set(["1.161.9", "1.161.12"])],
  ["@tanstack/react-router", new Set(["1.169.5", "1.169.8"])],
  ["@tanstack/router-core", new Set(["1.169.5", "1.169.8"])],
  ["@tanstack/router-generator", new Set(["1.166.45", "1.166.48"])],
  ["@tanstack/router-plugin", new Set(["1.167.38", "1.167.41"])],
  ["@tanstack/router-utils", new Set(["1.161.11", "1.161.14"])],
]);

const lockfilePath = new URL("../package-lock.json", import.meta.url);
const lockfileText = fs.readFileSync(lockfilePath, "utf8");
const lockfile = JSON.parse(lockfileText);

function getInstalledVersion(packageName) {
  return lockfile.packages?.[`node_modules/${packageName}`]?.version ?? null;
}

function vulnerabilityUrls(vulnerability, report) {
  const urls = [];
  const stack = [...(vulnerability.via ?? [])];

  while (stack.length > 0) {
    const item = stack.pop();
    if (typeof item === "string") {
      const linked = report.vulnerabilities?.[item];
      if (linked) {
        stack.push(...(linked.via ?? []));
      }
      continue;
    }
    if (item?.url) {
      urls.push(item.url);
    }
  }

  return urls;
}

function hasOfficialTanstackCompromise() {
  const affectedInstalled = [];

  for (const [packageName, affectedVersions] of affectedTanstackVersions) {
    const installedVersion = getInstalledVersion(packageName);
    if (installedVersion && affectedVersions.has(installedVersion)) {
      affectedInstalled.push(`${packageName}@${installedVersion}`);
    }
  }

  const hasPayloadRef = lockfileText.includes(tanstackPayloadRef);
  const hasSetupPackage = lockfileText.includes(tanstackSetupPackage);

  return {
    affectedInstalled,
    hasPayloadRef,
    hasSetupPackage,
    compromised:
      affectedInstalled.length > 0 || hasPayloadRef || hasSetupPackage,
  };
}

function isSuppressibleTanstackAudit(vulnerability, report) {
  if (!vulnerability.name?.startsWith("@tanstack/")) {
    return false;
  }

  const urls = vulnerabilityUrls(vulnerability, report);
  return urls.length > 0 && urls.every((url) => url === tanstackAdvisoryUrl);
}

const audit = spawnSync("npm", ["audit", "--json"], {
  cwd: new URL("..", import.meta.url),
  encoding: "utf8",
});

if (audit.error) {
  console.error(`Failed to run npm audit: ${audit.error.message}`);
  process.exit(1);
}

let report;
try {
  report = JSON.parse(audit.stdout);
} catch (error) {
  console.error("Failed to parse npm audit JSON output.");
  console.error(audit.stdout);
  console.error(error);
  process.exit(1);
}

const tanstackCompromise = hasOfficialTanstackCompromise();
if (tanstackCompromise.compromised) {
  console.error("Detected official TanStack compromise indicators.");
  for (const affected of tanstackCompromise.affectedInstalled) {
    console.error(`- affected package: ${affected}`);
  }
  if (tanstackCompromise.hasPayloadRef) {
    console.error(`- lockfile contains payload ref: ${tanstackPayloadRef}`);
  }
  if (tanstackCompromise.hasSetupPackage) {
    console.error(`- lockfile contains package: ${tanstackSetupPackage}`);
  }
  console.error(`See ${tanstackCompromiseAdvisory}`);
  process.exit(1);
}

const failures = [];
const suppressed = [];

for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
  const rank = severityRank.get(vulnerability.severity) ?? 0;
  if (rank < severityRank.get("high")) {
    continue;
  }

  if (isSuppressibleTanstackAudit(vulnerability, report)) {
    suppressed.push(vulnerability.name);
    continue;
  }

  failures.push(vulnerability);
}

if (failures.length > 0) {
  console.error("High or critical npm audit vulnerabilities remain:");
  for (const vulnerability of failures) {
    console.error(`- ${vulnerability.name}: ${vulnerability.severity}`);
  }
  process.exit(1);
}

if (suppressed.length > 0) {
  console.warn(
    "Suppressed overbroad TanStack npm audit advisory after checking official affected versions and IOCs:",
  );
  for (const packageName of suppressed.sort()) {
    console.warn(`- ${packageName}@${getInstalledVersion(packageName) ?? "unknown"}`);
  }
  console.warn(`Official advisory checked: ${tanstackCompromiseAdvisory}`);
}

console.log("npm audit passed for high/critical vulnerabilities.");
