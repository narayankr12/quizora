# Quizora 📝

**Turn any MCQ PDF into an interactive quiz in seconds.**

Upload a PDF of multiple-choice questions, and Quizora extracts every question, shows it one at a time, checks your answer instantly, explains the solution, and gives you a final score.

🔗 **Live demo:** https://namo-papa.onrender.com/

---

## ✨ Features

- **PDF → Quiz:** upload a text-based PDF (up to 20 MB); questions, options, correct answers and explanations are extracted automatically.
- **Instant feedback:** pick an option, see if it is right, and read the detailed solution.
- **Score & results:** progress bar during the quiz and a summary at the end.
- **Sample quiz:** one-click "Java Assignment" quiz to try the app without uploading anything.
- **Visit counter:** total page visits stored in PostgreSQL (no tracking, no personal data).
- **Name capture:** users enter only their name before starting. No passwords, no accounts.
- **Owner dashboard (`/admin`):** password-protected page showing submitted names with timestamps.

## 🛠️ Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.12 (standard library `http.server`) |
| PDF parsing | [`pypdf`](https://pypi.org/project/pypdf/) |
| Database | PostgreSQL via [`psycopg`](https://www.psycopg.org/psycopg3/) (JSON-file fallback for local use) |
| Frontend | Plain HTML, CSS and JavaScript (no framework) |
| Deployment | Docker on Render |

## 📁 Project Structure

```
quizora/
├── quiz_server.py        # HTTP server, PDF parsing, API, database logic
├── pdf_quiz_site.html    # Main quiz UI
├── admin.html            # Owner dashboard UI
├── java_mcq_quiz.html    # Sample quiz data
├── requirements.txt      # Python dependencies
└── Dockerfile            # Container build for deployment
```

## 🚀 Run Locally

```bash
git clone https://github.com/narayankr12/quizora.git
cd quizora
pip install -r requirements.txt

# optional
export QUIZ_ADMIN_PASSWORD="choose-a-strong-password"
export DATABASE_URL="postgresql://user:password@host:5432/dbname"

python quiz_server.py
```

Open **http://localhost:8002**.

Without `DATABASE_URL`, the app stores visits and names in local JSON files, which is fine for development.

> Use **Python 3.12 or older**. The server uses the `cgi` module, which was removed in Python 3.13.

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | *(empty)* | PostgreSQL connection string. If empty, JSON files are used |
| `QUIZ_ADMIN_PASSWORD` | *(empty)* | Password for `/admin`. If empty, the dashboard is disabled |
| `INITIAL_TOTAL_VISITS` | `0` | Starting value of the visit counter (applied once, when the table is first created) |
| `PREVIOUS_USER_COUNT` | `0` | Users counted before this database was set up (shown only in `/admin`) |
| `PORT` | `8002` | Port to listen on |
| `HOST` | `0.0.0.0` | Interface to bind to |
| `DATA_DIR` | project folder | Where the JSON fallback files are stored |

> Never commit `DATABASE_URL` or your admin password. Set them only as environment variables on your host.

## 🐳 Deploy on Render

1. Create a **PostgreSQL** database on Render and copy its internal connection URL.
2. Create a **Web Service** from this repo (Docker runtime, uses the included `Dockerfile`).
3. Add environment variables: `DATABASE_URL`, `QUIZ_ADMIN_PASSWORD`.
4. Deploy. The server creates the `quiz_users` and `quiz_stats` tables automatically on first start.
5. Health check path: `/healthz`

## 🔌 API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/` | – | Quiz page |
| `GET` | `/api/visit` | – | Counts a visit and returns `{ "visits": N }` |
| `GET` | `/api/stats` | – | Returns the current count without incrementing |
| `GET` | `/api/sample` | – | Built-in sample questions |
| `POST` | `/api/extract` | – | Upload a PDF (`multipart/form-data`, field `pdf`) and get questions as JSON |
| `POST` | `/api/register` | – | Save the user's name |
| `GET` | `/healthz` | – | Health check |
| `GET` | `/admin` | – | Owner dashboard page |
| `POST` | `/api/admin/login` | password | Start an owner session |
| `GET` | `/api/users` | owner | List submitted names |
| `POST` | `/api/admin/logout` | owner | End the owner session |

## 📄 Supported PDF Format

Each page should contain:

- `Question <number>:` followed by the question text
- Four options labelled `a.` `b.` `c.` `d.`
- `Correct Answer: <a-d>`
- *(optional)* `Detailed Solution: ...`

Scanned/image-only PDFs are not supported (no OCR yet).

## 🗺️ Roadmap

- [ ] OCR support for scanned PDFs
- [ ] Support for more PDF layouts
- [ ] Unique-visitor counting instead of raw page loads
- [ ] Quiz history and leaderboard

## 🔒 Privacy

Quizora stores only a total visit count and the names users type in. Names are visible only to the owner through the password-protected dashboard.

## 🤝 Contributing

Issues and pull requests are welcome.

## 📜 License

Add a license of your choice (e.g. MIT) to this repo.
