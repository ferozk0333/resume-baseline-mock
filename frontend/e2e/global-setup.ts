import { execFileSync } from "node:child_process";
import path from "node:path";

// Writes the sample resume PDF used by the tests (same text the fake extractor expects).
export default function globalSetup() {
  const root = path.resolve(__dirname, "../..");
  execFileSync(path.join(root, ".venv/bin/python"), ["-c",
    "from tests.helpers import make_pdf, SAMPLE_RESUME_LINES; import pathlib; " +
    "pathlib.Path('frontend/e2e/.tmp').mkdir(exist_ok=True); " +
    "pathlib.Path('frontend/e2e/.tmp/cv.pdf').write_bytes(make_pdf(SAMPLE_RESUME_LINES)); " +
    // A LinkedIn PDF the fake reader takes a few seconds over (early Review, specs/18 phase 3)
    "from tests.ui_fakes import SLOW_LINKEDIN_LINES; " +
    "pathlib.Path('frontend/e2e/.tmp/linkedin-slow.pdf').write_bytes(make_pdf(SLOW_LINKEDIN_LINES))"], { cwd: root });
}
