# Comprehension-Time (TTA) Study App  —  v5

Multilingual reading-comprehension experiment matching the protocol of
**Jain et al. (2024) §8.7**. Measures **Time-to-Answer (TTA)** for three
viewing conditions across English, Arabic, and Turkish.

## What changed in v5

v5 restructures the experiment to match the Jain et al. (2024) protocol
verbatim and the proposal §4.4.3:

- **Between-subjects design.** Each participant is assigned ONE language
  AND ONE condition by the admin. They answer all 50 questions in that
  single condition — the same participant never sees the same question
  twice.
- **Same 50 questions across all conditions in a language.** A
  language-level pool seed ensures the 50 questions are identical for
  all participants in that language; only the *condition* (passage /
  mindmap / both) differs across participants. This enables paired
  t-tests matched on `(sample_id, qid)`.
- **Model alternation within mindmap/both.** Gemini and Qwen alternate
  per question (~25 each), allowing secondary model comparison without
  splitting cells.
- **Built-in paired t-tests** in the admin dashboard.
- **Recommended minimum: 9 participants** (1 per language × condition cell).

## Study design at a glance

```
Languages: English, Arabic, Turkish      (3)
Conditions: passage, mindmap, both       (3)
Participants per cell: 1 (minimum)
Total participants: 9
Questions per participant: 50
Total responses: 450 (9 × 50)
```

| Language | Passage | Mindmap | Both |
|----------|---------|---------|------|
| English  | 1 person | 1 person | 1 person |
| Arabic   | 1 person | 1 person | 1 person |
| Turkish  | 1 person | 1 person | 1 person |

The same 50 questions in each language are seen by all 3 participants
(in 3 different conditions). No participant sees the same question twice.

## Layout

```
comprehension_app/
├── app.py
├── requirements.txt
├── README.md
├── analysis.ipynb              (offline statistical analysis notebook)
├── .streamlit/secrets.toml
└── data/
    ├── en_manifest.json   (50 records, 243 questions)
    ├── ar_manifest.json   (50 records, 208 questions)
    ├── tr_manifest.json   (50 records, 131 questions)
    ├── participants.json
    └── images/
        ├── ar/{01..50}_{gemini,qwen}.webp
        ├── en/{01..50}_{gemini,qwen}.webp
        └── tr/{01..50}_{gemini,qwen}.webp
```

## Local run

```bash
cd comprehension_app
pip install -r requirements.txt
streamlit run app.py
```

- Participant interface: `http://localhost:8501`
- Admin dashboard: `http://localhost:8501/?admin=1`
- Admin password: **`NoorAleman_171961`** (in `.streamlit/secrets.toml`)

## Streamlit Cloud deployment

1. Push to GitHub (gitignore `.streamlit/secrets.toml`)
2. share.streamlit.io → New app → main file: `comprehension_app/app.py`
3. Settings → Secrets, paste contents of `.streamlit/secrets.toml`
4. Deploy

## Adding participants

In the Admin → Participants tab, add each participant with **3 fields**:

1. **Name** (e.g., `khaled`)
2. **Language** (`en`, `ar`, or `tr`)
3. **Condition** (`passage`, `mindmap`, or `both`)

Or pre-define them in `.streamlit/secrets.toml`:

```toml
[participants.khaled]
language  = "ar"
condition = "passage"
```

## How metrics are computed

**TTA** = wall-clock seconds from question render to Submit click. Stored
per trial as `tta_seconds`.

**Accuracy** = case-insensitive substring match between participant
answer and gold answer (either contains the other counts as correct).

**Speedup** =
```
(mean_TTA_passage − mean_TTA_mindmap) / mean_TTA_passage × 100
```

**Paired t-test** = for each language, each (sample_id, qid) is matched
across two conditions (different participants saw it in different
conditions). The paired t-test operates on these N matched pairs:

```python
from scipy import stats
# x1 = TTAs from passage participant on questions 1..50
# x2 = TTAs from mindmap participant on questions 1..50
t_stat, p_value = stats.ttest_rel(x1, x2)
```

## Replicating Jain et al. (2024)

The reference paper reports a **31.9% speedup** for English mind maps
vs text-only. Your study extends this to Arabic and Turkish. Expected
reporting:

> *"Mind maps reduced TTA by an average of XX.X% relative to text-only
> presentation, comparable to the 31.9% reported by Jain et al. (2024).
> Paired t-tests confirmed the difference was statistically significant
> in all three languages: English (t(49) = X.XX, p < .001), Arabic
> (t(49) = X.XX, p < .001), Turkish (t(49) = X.XX, p < .001)."*

## Statistical analysis

Use the **Statistical tests** tab in the admin dashboard, or run the
included `analysis.ipynb` notebook on the exported CSV for more advanced
analyses (mixed-effects models, etc.).

## Storage backends

- **Google Sheets** (preferred for production) — durable, multi-user,
  easy to back up. **Use this for any real participant session.**
- **SQLite** (local file `tta_local.db`) — fine for testing on your laptop, but **dangerous** on Streamlit Cloud.

> ⚠️ **Streamlit Community Cloud wipes the local file system** on every container
> restart (≈ every 30 min of inactivity, on every redeploy, and whenever the
> platform reschedules your app to a new machine). If you rely on SQLite, **all
> collected TTA responses will eventually be lost**.
>
> For any real participant session — even a single one with paid researchers — you
> **must** configure the Google Sheets backend before sharing the app URL.

## Limitations to note in your thesis

> *"Due to resource constraints, we used the minimum design implied by
> Jain et al. (2024), with one participant per (language × condition)
> cell. While our paired analysis treats each question as a matched
> observation, the small participant pool limits inferential power for
> participant-level variance. Future work should scale to 5+
> participants per cell."*
