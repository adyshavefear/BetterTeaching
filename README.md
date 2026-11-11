# BetterTeaching

Work in progress: a Python assistant that reads grade sheet images with Gemini,
matches students to the current school portal page, and fills three grade fields
per student using Playwright. Review the extracted results before confirming.

## Setup

Use Python 3.10 or later:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set `GEMINI_API_KEY` in your environment or a local `.env` file. Never commit
credentials. Start a Chromium browser with a separate debugging profile:

```bash
chromium --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=/tmp/betterteaching-browser
python script_notas.py /path/to/grade-sheet.jpeg
```

Sign in manually and open the class grade page in the first browser tab before
continuing at the terminal prompt. Close the debugging browser when finished.
Keep its debugging port restricted to the local computer.

## Data handling and limitations

Run `python -m unittest -v` to check validation and field selection without
calling Gemini or connecting to the school portal. The complete response and
all target fields are checked before filling starts. Unknown or repeated student
IDs, mismatched names, invalid grades and missing or ambiguous fields stop the
operation. Field positions are never used to guess a grade column.

- The script uploads the supplied images and sends student names and portal IDs
  to Google's Gemini API. Only run it with data you are authorized to process
  through that external service. Git exclusions do not prevent these uploads.
- Local images, environment files, saved portal pages, virtual environments, and
  logs are excluded from Git. Logs and terminal output can contain student names
  and grades; protect them locally.
- Only the script and the explicitly allowed setup/documentation files are
  included in the repository. Review any future changes before publishing.
- This is a prototype tied to portal selectors and the configured Gemini model.
  Model availability, student matching, and live portal behavior have not been
  verified here. Review every match and the selected term before confirming;
  change events may trigger portal persistence.
- No school records or real student images are distributed with this project.
