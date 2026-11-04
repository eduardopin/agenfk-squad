// Staging of files an actor asks the browser to upload. Kept apart from the daemon so it can be tested without a browser.
import { closeSync, constants, existsSync, fstatSync, mkdirSync, openSync, readFileSync, readlinkSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { basename, join, sep } from "node:path";

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const REFUSE = { ok: false, code: "privileged", text: "upload: only files in the actor's own folder can be uploaded" };

/**
 * The driver reads the file, not the actor: with an actor token it may only hand over files from the actor's own
 * folder, or a jailed actor could make the browser upload any host file to the product. The bytes are copied at check
 * time (no symlink followed, same inode as the checked path) into a driver-only folder, and that copy is what the
 * browser uploads, so swapping the path after the check changes nothing.
 * Returns { ok: true, paths } with the staged copies, or the refusal ({ ok: false, code: "privileged", text }).
 */
export function stageUploads(actorDir, uploadsDir, files, maxBytes = MAX_UPLOAD_BYTES) {
  let root = null;
  try { root = realpathSync(actorDir); } catch {}
  const paths = [];
  for (const f of files) {
    let data = null;
    try {
      const real = realpathSync(f);
      if (!root || !real.startsWith(root + sep)) return REFUSE;
      const fd = openSync(real, constants.O_RDONLY | constants.O_NOFOLLOW);
      try {
        const st = fstatSync(fd);
        if (!st.isFile() || st.size > maxBytes) return REFUSE;
        // Judge the file actually opened, not a name: on Linux the kernel reports the open descriptor's path, so a
        // directory swapped between the check and the open is caught. Elsewhere (no jail exists there) compare inodes.
        if (existsSync("/proc/self/fd")) {
          if (!readlinkSync(`/proc/self/fd/${fd}`).startsWith(root + sep)) return REFUSE;
        } else {
          const now = statSync(real);
          if (st.ino !== now.ino || st.dev !== now.dev) return REFUSE;
        }
        data = readFileSync(fd);
      } finally { closeSync(fd); }
      const dir = join(uploadsDir, randomBytes(8).toString("hex"));
      mkdirSync(dir, { recursive: true, mode: 0o700 });
      const copy = join(dir, basename(real));
      writeFileSync(copy, data, { mode: 0o600 });
      paths.push(copy);
    } catch {
      return REFUSE;
    }
  }
  return { ok: true, paths };
}
