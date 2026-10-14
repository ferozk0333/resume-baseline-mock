import { execFileSync } from "node:child_process";
import path from "node:path";

// Writes the sample resume PDF used by the tests (same text the fake extractor expects).
export default function globalSetup() {
  const root = path.resolve(__dirname, "../..");
  execFileSync(path.join(root, ".venv/bin/python"), ["-c",
    "from tests.helpers import make_pdf, SAMPLE_RESUME_LINES; import pathlib; " +
    "pathlib.Path('frontend/e2e/.tmp').mkdir(exist_ok=True); " +
    "pathlib.Path('frontend/e2e/.tmp/cv.pdf').write_bytes(make_pdf(SAMPLE_RESUME_LINES))"], { cwd: root });
}
