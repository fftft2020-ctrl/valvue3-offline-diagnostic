# Offline project contract

## Authorization and report status

Stage 1 materials, Stage 2 parameters, Stage 3 diagnosis and Stage 5 DOCX content
are different decisions. Before any instruction below to submit and stop, check
whether the current object already has valid authorization. Continue within that
scope; do not ask again merely because the task resumed. Never apply approval of
one object to changed inputs or an unapproved new diagnosis version.

Stage 5 normally asks the user to confirm the actual DOCX. An explicit instruction
delegating content review and delivery to the Agent is also actionable: retain the
verbatim delegation, scope, actual DOCX hash and Agent check results in the existing
task record. Record this as Agent-checked and user-authorized, not human-calibrated.
A generic continuation request is not such a delegation. No new approval CLI is
introduced, and the existing input, freeze and delivery checks remain required.

Keep three facts separate: user authorization, who checked the content, and artifact
validation. The historical filenames `人工校验版` and `最终商务版`, generation success,
an ingested file, and `wps_delivery_validated` do not prove human review. Describe
Agent-generated review files as “待人工校验” until an actual human return or review
exists. New review filenames and cover labels use `待人工校验版`; allocation counts
both old and new names in the same version sequence. Preserve historical files and
bindings, including the content of format-only children.

## Required layout

```text
<project>/
├── project.toml
├── raw/<valve-tag>/
├── specs/                     # optional; PDFs may be flat or under specs/<valve-tag>/
├── inbound/                   # optional
├── experience/
│   ├── feedback_inbox/
│   ├── extraction_overrides.json          # confirmed parameter corrections
│   └── diagnostic_summary_overrides.json  # optional, confirmed customer-facing per-valve summaries
├── work/
│   ├── stage-gates/
│   │   ├── material-archive-report.json/.md       # Stage 1 frozen archive snapshot
│   │   ├── material-archive-report.html           # Stage 1 self-contained browser review
│   │   ├── materials-approval.json                # Stage 1 user approval
│   │   ├── parameter-extraction-report.json/.md/.html # Stage 2 approval snapshot
│   │   ├── parameters-approval.json               # Stage 2 user approval
│   │   ├── diagnosis-review-report.json/.md       # Stage 3 compact diagnosis review
│   │   └── diagnosis-approval.json                # Stage 3 user approval and freeze binding
│   ├── material-readiness/
│   │   ├── readiness.json           # machine-readable current material inventory
│   │   └── readiness.md             # human-readable readiness report
│   ├── ocr/                         # hash-addressed PDF text/OCR cache
│   ├── intake-unpacked/<sha256>/    # safely unpacked supplementary archives
│   ├── evidence-extraction/
│   │   ├── project-index.json
│   │   ├── extraction.md
│   │   ├── supplementary-materials.json
│   │   ├── supplementary-materials.md
│   │   ├── review-queue.json
│   │   ├── codex-review-prompt.md
│   │   ├── codex-review-schema.json
│   │   ├── codex-review-results.json      # present after visual review
│   │   ├── review/                        # rendered PDF pages for review
│   │   ├── run-manifest.json
│   │   └── valves/<valve-tag>.json
│   ├── review/<version>/            # snapshot, change list, human overrides
│   ├── business/<version>/          # business run snapshot and manifest
│   ├── data/<period>/vN/             # data-only run snapshot and manifest
│   ├── run_history.jsonl            # append-only internal trace
│   ├── formal_diagnosis/runs/<run>/  # frozen source required by diagnostic reports
│   ├── manual_curve_interpretations.json
│   ├── analysis_summary.json         # current prepared report evidence
│   └── run_manifest.json             # current shared-entry manifest
└── output/
    ├── review/                       # versioned DOCX + validated same-name WPS PDF
    ├── business/                     # versioned DOCX + validated same-name WPS PDF
    └── data/                         # period-scoped DOCX + validated same-name WPS PDF
```

Customer projects contain data and configuration, not engine copies or a separately maintained experience library. The full experience text is saved automatically as a diagnostic input, not maintained in the project. New projects do not create an experience log or learning-status ledger.
The tree shows default paths. Prepared summaries, report images and run manifests
follow the effective `paths.work` / active period override; resolve them through
`load_project`, not by assuming `work/analysis_summary.json` or deriving the project
root from a summary's parent. Stage gates, evidence-extraction, versioned review,
format-revision and WPS-validation directories remain under the project's `work/`
as implemented. Verify actual command outputs before selecting an artifact.
Unified OCR schema v2 stores each document at `work/ocr/documents/<source_kind>/<sha256>.json`.
Diagnostic and specification uses of identical bytes retain separate text/layout results. Old cache files stay untouched; re-extract materials to prepare v2, then review parameters again. The cache schema is bound into Stage 2 approval fingerprints.
Active projects belong under the Git-ignored `离线诊断/projects/`; the Git-ignored
`离线诊断/development/` is a non-production root for one-off layout prototypes,
manual DOCX/PDF/PNG artifacts, and comparison renders. A development experiment
must not contain a production `project.toml`, be passed to the shared report entry,
or be registered as a delivery. Its artifacts are promoted only by implementing the
approved general change in this Skill and regenerating from the production project;
they are never copied directly into production. A production project's `work/`
holds pipeline state, required checks and task-related temporary scripts/review
copies; those temporary items are not delivery artifacts. One-off prototypes stay
in `development/`, while confirmed-content revisions follow the format-only route. Versioned
review reports created by that entry remain production artifacts while awaiting
approval and do not move to the development root.
The Git-ignored
`离线诊断/archive/` is read-only and is never a runtime project root. The legacy
`离线诊断/case_library/` is local historical material; formal diagnosis reads only
the sole common experience prose shipped inside this Skill.
LibreOffice renderings belong under `work/pdf-checks/` and are internal validation
artifacts. They must never be copied into these delivery directories or registered as
delivery PDFs.

## Required diagnostic flow

### Material-readiness report

For a new project, changed bound materials, or an explicit restart from scratch,
run `--prepare-materials` first. Resuming a task keeps current valid stage approvals
and continues from the first incomplete or invalid stage. Initialization, read-only
report audits, final manual-review ingestion, confirmed-content formatting revisions,
and WPS-only validation follow their specific routes. It inventories `inbound/`, `raw/`, and `specs/`, hashes
regular files, and writes both readiness files under `work/material-readiness/`.

The first inventory must not trigger OCR or infer report types from filenames.
When native text contains an explicit `ValvXpert`, `DTM Report`, or
`Active Report` title, that source identity takes precedence over generic curve
or task-summary content on the same PDF. A recognized ValvXpert/DTM candidate
is shown as pending Stage 2 extraction, never as missing DTM data.
Without a current evidence-extraction index, report types, curves, and
specification matches remain pending. A later run prefers the fresh
`work/evidence-extraction/project-index.json`; a legacy matching analysis summary
is a compatibility fallback only. The refreshed report adds per-valve report
counts, curve counts and execution status, matched Specification/air-piping/accessory
page roles, operating context, and consistency warnings. Missing or pending items
are visible data gaps, not workflow gates.

When the project points to a frozen formal-diagnosis run, Stage 1 must also
compare each current valve's raw source hashes and bound specification source
hashes with that baseline. The report lists unchanged candidates and requires an
explicit project choice: `reuse_unchanged` or `reanalyze_all`. Approval is
invalid when unchanged candidates exist but no choice was recorded. Source
identity is only a preliminary reuse signal; Stage 2 must still compare the
actual parameter values, statuses, units, conflicts, and technical-page physical
bindings.

The frozen Stage 1 snapshot must also render a self-contained
`material-archive-report.html` from the same JSON payload. Serve that exact file
locally and through Tailscale Serve only; never use Tailscale Funnel, Sites, or
another public host. When these services are available, include both clickable URLs and request both
successfully before reporting them. If mobile review was not requested and the
service is unavailable, provide the current local HTML/Markdown for review.
An explicitly requested mobile endpoint remains a deliverable to repair or
report as blocked; never replace it with public hosting. The HTML is an
approval surface only: publishing it does not approve the gate and does not
permit Stage 2 work.

### Material evidence extraction

After confirmed inbound files have been normalized, the standard second
operation is `--extract-materials`. It prepares or reuses the project OCR cache,
parses native and structured formats first, extracts per-valve factual
parameters, records all candidate sources and units, emits supplementary,
missing, conflict, and review lists, and refreshes the readiness report.
`--prepare-ocr` remains a low-level compatibility and troubleshooting command;
it is not a prerequisite for the standard operation.

Stage 2 produces both a per-valve primary parameter matrix and complete factual
JSON. It also writes `work/stage-gates/parameter-extraction-report.md` and
`.json` for user review. The Markdown is a compact approval interface: project
summary, one key-parameter row per valve, per-valve review status, grouped
decisions, and known non-blocking gaps. It must not expand the complete matrix,
long JSON values, per-field source paths, supplementary inventory, or repeated
missing-field bullets; those remain in the JSON and internal extraction files.
Known source absence or an empty source field is a definite gap, not an
uncertainty; only unresolved review items and semantic conflicts are presented
as pending decisions.
The report is submitted and the workflow stops until a project-level approval
is recorded with `--approve-parameters`; the approval is stored in
`parameters-approval.json`. Missing values may be knowingly approved, but their
reason and downstream impact remain visible. Continuous Diagnostics is shown as
`无数据` in the human-readable report; its more specific reason code is retained
only in JSON.

The Stage 2 report carries the Stage 1 reuse policy and lists which candidates
remain parameter-identical. `reuse_unchanged` permits reuse only at the evidence
layer whose strict hashes match; whole-valve diagnosis inheritance still
requires equality of the complete formal evidence, observations, and loaded experience text. `reanalyze_all` forbids inherited observations and
whole-valve conclusions.

The extraction run must not create ratings, curve-shape interpretations,
whole-valve conclusions, or actions. It must not read a legacy analysis summary,
frozen diagnosis, old rating, or old report as current evidence, and must not
overwrite them. Exact page text/OCR caches may be reused only when their source
hash and OCR strategy version still match. Historical reports remain internal
supplementary material unless the user explicitly requests a named historical
reference.

Only low-confidence, degraded visual evidence, unreliable table relationships,
or uncertain page roles are written to `review-queue.json` for visual review.
High-confidence values and new pages whose automatic assignment is reliable are
not reviewed merely because they are new.
Only queue entries with a current `image_path` may be reviewed through
`run_metered_codex.py`, using `codex-review-prompt.md` and
`codex-review-schema.json`. The result is saved as
`codex-review-results.json` and consumed on the next `--extract-materials` run
only while its review ID and source SHA-256 remain current. Codex may confirm
visible facts but must not resolve competing sources by inference. Confirmed
manual corrections belong in `experience/extraction_overrides.json`, require a
reason, and take precedence without deleting original candidates.

Any report that contains ratings, curve interpretations, whole-valve conclusions, or actions must follow this order:

```text
material inventory and readiness report
→ verify inbound assignments and normalize into raw/<valve-tag>/
→ hash original files
→ obtain current Stage 1 material archive approval
→ run material evidence extraction (native/structured parsing, then OCR as needed)
→ review missing/conflicting/low-confidence fields and record explicit overrides
→ match specifications and retain measurements, active faults, and task status
→ obtain project-level Stage 2 parameter approval
→ prepare one complete evidence/model request per valve
→ pass all valve evidence, the entire experience text, curves and technical-page images
  to one metered codex exec call
→ have that model produce separate objective-observation and engineering-judgment
  fields plus the whole-valve rating and conclusion in the same response
→ save all per-valve diagnosis drafts
→ submit one compact project diagnosis review and obtain user approval
→ freeze each valve run
→ generate DOCX and an internal LibreOffice check PDF
→ complete content acceptance and current Stage 5 confirmation / explicit delegation
→ export the delivery PDF with WPS through the shared RPC exporter
→ validate WPS layout and visually review its contact sheet
→ register delivery artifacts
```

### Final human-calibration fast path

Before ingestion, identify the actual document sent to the reviewer and retain its
path, SHA-256, delivery manifest and frozen-run binding. Pass `--review-base` explicitly.
The importer validates registered review/business reports and format-only ancestry;
it never chooses the highest version. The implicit compatibility path is allowed only
when exactly one review DOCX exists. Wrong projects, changed bytes, ambiguous sources
and a different current frozen run are rejected before archiving.
Returns are stored under `work/review/returns/<base-and-return-hash>/`; repeated imports
are idempotent. Legacy `work/review/vN/` remains readable. Multiple matching returns
require an explicit `--review-return` change list during business generation.
See [human feedback learning](human-feedback-learning.md) for direct Agent distillation of human corrections into the single prose library, full-text loading, and automatic input snapshots.

When the report has already completed diagnosis and content generation and the
user returns its final human-calibrated DOCX, this is a Stage 5-to-6 operation,
not a new diagnostic run. Run only the existing review ingestion, business
finalization, WPS export, automatic delivery check, and one representative-page
visual review. Do not inventory materials, OCR, extract parameters, prepare
curves, call per-valve models, regenerate the review report, or change the
shared engine merely to finish one project.

Stage 5 has no fourth approval CLI or approval file. Content confirmation is the
user's explicit conversation confirmation of a named current base DOCX, or a
returned-file instruction that explicitly authorizes that file as the final content.
Keep the verbatim instruction and base/returned file version reference in the existing
task record. A returned DOCX by itself is not confirmation. `--ingest-review` archives
the original and binds its DOCX/hash/frozen-run identities; it does not record approval
text or replace explicit content confirmation.

Use the same current report period for both commands; omit `--report-period`
only when the project does not use a period override:

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "<project>" --report-period "<current-period>" \
  --ingest-review "<human-return.docx>" --review-base "<actual-sent-base.docx>" &&
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "<project>" --report-period "<current-period>" \
  --generate-business --review-return "<registered-change_list.json>"
```

The human-edited per-valve rating, conclusion, issues, actions, and curve text
are authoritative. Rating counts, rating lists, overall conclusions, priority
lists, and overview tables are derived and must be synchronized from those
per-valve values. Preserve all media, tables, bookmarks, links, and embedded
objects. Only explicitly authorized typo/formality cleanup and configured title
or report-date changes may alter other text.

Keep model context small: consume compact manifest counts, `workflow-summary.json`, and unresolved-item
summaries, never dump the full DOCX, complete diff, media-hash list, or large
JSON into chat. If deterministic checks show zero unresolved changes and stable
objects/media, do not re-read curves. After WPS export, inspect one contact
sheet containing the required representative pages in one metered `codex exec` call.
Use a format-only child for a font or layout compatibility fix. Modify shared
code and run the full portable regression only when a reproducible shared-code
defect blocks this path.

### Confirmed-content formatting revision

The diagnostic flow above is not rerun when the user has already confirmed the
report content and requests formatting changes only. In that case the only
permitted flow is:

```text
confirmed parent DOCX
→ copy to the next non-overwriting report version
→ record parent file hash and formatting-insensitive content identity
→ edit layout properties only
→ verify text structure, media hashes, bookmarks, links, and embedded objects
  are identical to the parent
→ export the candidate with WPS
→ run complete WPS layout and visual review
→ register the child delivery with its parent reference
```

Use `--prepare-format-only <confirmed-parent.docx>` to create the child. This
operation must not call report generation, OCR, specification matching,
diagnosis, rating, or curve-review code. At delivery validation, pass
`--format-only-source <confirmed-parent.docx>`; a prepared child is also
automatically bound to the parent recorded in
`work/format-revisions/<candidate-stem>/format_revision_manifest.json`.
Any content-identity mismatch is a hard delivery failure.

### WPS delivery command sequence

Use the current report period consistently in every command below, or omit
`--report-period` throughout when the project has no period override. First
export the confirmed DOCX and run validation without visual approval; this
creates `work/wps-validation/<report-stem>/contact-sheet.png` and records any
additional device-overview contact sheets in the pending manifest:

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "<project>" --report-period "<current-period>" \
  --export-wps-docx "<confirmed-or-format-child.docx>"

python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "<project>" --report-period "<current-period>" \
  --delivery-docx "<confirmed-or-format-child.docx>" \
  --validate-wps-pdf "<same-directory-and-stem.pdf>" \
  --format-only-source "<confirmed-parent.docx>"  # format-only child only
```

Write a short review prompt under this validation directory, then inspect the
contact sheet in one metered `codex exec`; repeat `--image` for every additional
device-overview contact sheet named by the manifest:

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_metered_codex.py run \
  --project-root "<project>" \
  --stage delivery_visual_review \
  --call-id "<report-stem>.delivery-layout.attempt-1" \
  --cwd /tmp --ignore-user-config --ignore-rules \
  --prompt-file "<project>/work/wps-validation/<report-stem>/visual-review-prompt.md" \
  --output-last-message "<project>/work/wps-validation/<report-stem>/visual-review-result.md" \
  --image "<project>/work/wps-validation/<report-stem>/contact-sheet.png"
```

The prompt must require actual inspection of the cover, overview, first/middle/last
valve pages, curve pages, Specification/air-piping representatives, and ordinary
accessory representatives when enabled. Only after reading that result, rerun the
same validation command with the truthful visual note to register delivery:

```bash
python .agents/skills/valvue3-offline-diagnostic/scripts/run_offline_report.py \
  --project "<project>" --report-period "<current-period>" \
  --delivery-docx "<confirmed-or-format-child.docx>" \
  --validate-wps-pdf "<same-directory-and-stem.pdf>" \
  --format-only-source "<confirmed-parent.docx>" \
  --approve-wps-layout \
  --wps-review-note "<pages actually inspected and result>"
```

For a format-only child, `--format-only-source` is required in both validation
calls and must name the same confirmed parent. Omit it from both calls for an
ordinary confirmed DOCX. Registration succeeds only when the resulting
`delivery_manifest.json` has status `wps_delivery_validated`.
For `--assemble-units` volumes, validation uses the sibling `分册清单.json` to bind
the DOCX hash, that volume's valve tags and ratings, and substantive DOCX text
coverage in the PDF. The same WPS layout and representative-page visual checks
still apply; the unrelated active project batch is not used as the volume scope.

### Integrated diagnosis and freeze

Objective observation and engineering diagnosis remain separate data fields, but a fresh
diagnosis uses one model call per valve rather than two model stages. That call receives
all current structured specifications, measurements, faults, task status, the complete
experience text and every valve image. The script may bind and validate the returned fields;
it must never calculate or rewrite the model's curve ratings, whole-valve rating,
severe-impact statement, counterevidence or actions. A main chart or one matching image is
never enough to establish a whole-valve diagnosis.

Level 5 is reserved for cases where the available evidence is insufficient to
judge the whole valve's impact. A missing or failed actuator performance/signature
test does not by itself justify Level 5 when successful step, ramp, or equivalent
independent evidence already supports a whole-valve assessment. Rate from the
remaining evidence, disclose the missing performance channel and any mode-switch
failure as uncertainty. Recommend a targeted retest only when needed for a key
judgment and feasible on site. Required safety or post-repair verification still
states its conditions and the restrictions that apply until it is completed.

For a project run, every completed diagnosis remains a `05_diagnosis_draft.json`
until the project-level Stage 3 review is approved. Run
`prepare-diagnosis-review --run-root <run-root>` to validate every draft and write
the compact Markdown/JSON review under `work/stage-gates/`. The Markdown contains
one row per valve—rating, main conclusion, supporting evidence, counterevidence,
action and material uncertainty—and expands only active faults, non-success tests
and information gaps. It must not expand per-curve prose, internal paths, hashes
or full JSON. Stop after submitting this report to the user.

After explicit approval, run `approve-diagnosis` with `--run-root <run-root>`
and `--approval-text <verbatim-text>`. The command binds the approval to the generated
review and every diagnosis-draft hash, revalidates all targets before writing any
freeze output, and then freezes the complete project run. A changed draft,
evidence input or review report invalidates approval. Before approval, revise only
affected valve drafts and regenerate the project review; unchanged image and
observation hashes do not justify another visual read.

If freezing an approved run is interrupted, repeat `approve-diagnosis --run-root
<run-root>` without new approval text. The same-version approval retains its
original text and timestamp; completed frozen files are verified without being
overwritten, and pending targets resume. Changed inputs or damaged files are
rejected rather than repaired under the old approval.

Stage 3 follows `references/integrated-valve-diagnosis.md`. The main Agent
operates and checks serial model calls directly, without subagents. Each valve
uses one `gpt-6-sol` high `codex exec` call, as enforced by the current
submission code; this does not set the parent orchestration model. Every curve and technical
page for that valve is attached to the same call that reads the complete evidence
request. The tool validates model provenance, image SHA-256 coverage, observation versus
diagnosis field boundaries and concrete counterevidence. Threshold applicability and
evidence gaps remain model judgments under the integrated contract; keyword matches
do not decide whether a sentence asserts an abnormal metric. A project
helper or report-engine rating is not a valid diagnosis source.

When a human-confirmed report must preserve project-specific final ratings or customer-facing per-valve summaries,
store them in `experience/diagnostic_summary_overrides.json` and reference the file through
`paths.diagnostic_summary_overrides` in `project.toml`. Keep algorithm ratings and reusable diagnostic rules separate.
Use `rating_conclusion` for a human-confirmed chapter conclusion and
`health_rating_detail_mode = "summary_only"` when that chapter must preserve the concise two-row health table;
projects without this setting retain the detailed health table.

When a report directly consumes a completed single-valve diagnosis run, reference the frozen run directory through
`paths.formal_diagnosis_run`. The shared engine verifies every freeze hash, requires all configured report valves,
matches curve results to the current report images by SHA-256, and reads both per-curve interpretations and whole-valve
summaries from the same run. Each formal diagnosis must bind the original PDF/devdata file hashes, exact structured
evidence, observations, and the complete loaded experience prose through `diagnosis_basis`. Image equality alone is not
sufficient to inherit a diagnosis: inheritance is allowed only when all diagnostic inputs are equal and must be
recorded explicitly; otherwise a fresh diagnosis is required. A human summary or curve interpretation is a
final override only when registered against the actual review base and the same frozen-run identity;
configuration alone is insufficient.

Whole-valve inheritance follows the strict [reuse rules](#reuse-and-version-rules);
it is not a way to fill or mix fields in a new model result. Create and advance the run only with
`scripts/formal_diagnosis_tools/single_valve_workflow.py`. Its sole experience
source is `references/common-offline-experience.md`. It automatically saves the entire text
as `00_experience.md` for each valve and includes it verbatim in the model prompt and request.
No experience-version choice, filtering, adoption IDs, unused-item reasons, or extra experience approvals are required.
Historical customer libraries and project experience JSON are not loaded. Old frozen runs remain read-only compatible. Direct `submit-diagnosis` remains a low-level single-valve
operation; a project-bound run rejects it until the current project diagnosis
review is approved, and the submitted payload must exactly match the approved
draft. `approve-diagnosis` advances all matching project targets to `frozen`.
When all targets are frozen, the project manifest becomes directly
consumable by the report loader. Do not edit either manifest to simulate this
transition.

The deterministic `structured_evidence` bound by formal diagnosis must include all
current device and specification fields, field status/conflicts/uncertainty, operating
context, all original-file SHA-256 values, and the physical identities of the selected
Specification and air-piping pages. For Sequencer evidence it must additionally include
the full start/end time window; option configuration, device mode, valve position,
manual setpoint, input signal, board/min/max temperatures; strokes, cycles, time open,
time closed and time near closed; active and historical faults; the complete Task
Execution Summary; and the separate max/average/min values for Run Ramp hysteresis,
dead band and linearity. Non-success tasks are a derived view, not a replacement for
the complete task list. A legacy combined `Hysteresis + Dead Band` value is compatibility
input only and must not be generated or shown in new reports.

The diagnosis semantic hash covers values, units, extraction status, task results,
faults and evidence-page identities. It excludes absolute workspace paths, generation
times, temporary image/OCR-cache paths and output version numbers. A change to any
bound semantic field requires a child diagnosis version and a fresh whole-valve
diagnosis. Equal image SHA-256, crop bounds and verified observation hash permit reuse
of that objective observation only; they do not permit inheritance of the whole-valve
diagnosis.

### Reuse and version rules

- Equal image SHA-256, crop bounds and verified observation hash permit reuse only of that objective observation.
- Whole-valve inheritance requires equality of original source files, complete structured evidence, objective
  observations, and the complete experience text saved with the diagnosis inputs.
- Any change to a source file, specification field, measurement, active fault, task status, observation, or loaded experience text requires a fresh whole-valve diagnosis.
- New or changed images, changed crop boundaries, or observations without completed visual review require a new
  visual inspection.
- `inherited_source_bound` is a one-time compatibility migration for a legacy frozen diagnosis that did not record
  source file hashes. It is not a general inheritance mode for new runs.
- A frozen run is immutable. A correction creates a child version with a parent reference and revision reason.
  Never edit frozen JSON in place.

## Input rules

- Stage 1 is file archiving only. Run `--prepare-materials` before evidence
  preparation and rerun it whenever a material-relevant project setting,
  `inbound/`, `raw/`, `specs/`, or a bound context/catalog file changes. Submit
  `work/stage-gates/material-archive-report.md` to the user and stop.
- Record any unambiguous user approval with `--approve-materials
  "<verbatim approval>"`. The approval binds the frozen archive report and
  current material-input fingerprint; missing materials may be explicitly
  accepted, but changed inputs invalidate the approval.
- Do not OCR, extract parameters, prepare curves/formal evidence, diagnose, or
  generate reports before Stage 1 approval. Configuration dry-run, existing
  report read-only checks, format-only revision, and WPS delivery validation
  are exempt.
- After approval, begin Stage 2 with `--extract-materials`.
  Read `extraction.md`, `supplementary-materials.md`, and `review-queue.json`;
  review only low-confidence or unreliable relationships; unresolved gaps remain
  explicit uncertainty and do not become guessed values. Submit
  `work/stage-gates/parameter-extraction-report.md` and stop until the user
  approves it with `--approve-parameters`. No curve preparation, formal evidence,
  diagnosis, freezing, or new report generation is allowed before a valid
  `parameters-approval.json` exists.
- The Stage 2 approval is project-level and binds the Stage 1 approval, the
  complete primary-matrix/evidence semantic fingerprint, extraction schema and
  normalization strategy, manual overrides, and consumed low-confidence reviews.
  Any change to those inputs invalidates it. Absolute paths, cache locations,
  timestamps, output version numbers, later curve observations, and later
  diagnosis results do not invalidate Stage 2 approval. Recompute only affected
  valves, but submit a new complete project report and obtain approval again.
- The shared engine consumes only direct files under `raw/<valve-tag>/`.
  Root-level raw files, nested valve content, empty files, and non-PDF/devdata
  files require review.
- Files remaining under `inbound/` are reported as unassigned, but do not
  automatically block evidence preparation.
- Read every original diagnostic file under `raw/`; do not diagnose from filenames or partial previews.
- Match optional specifications by tag, serial, catalog, header, or OCR evidence.
- Discover specification PDFs recursively. When a PDF is under `specs/<valve-tag>/`, preserve its
  relative path in the OCR cache and use the parent directory as matching evidence; duplicate
  basenames in different valve directories must remain distinct.
- Classify reliably matched pages with independent `specification`, `air_piping`, and
  `accessory` roles. Insert the main Specification / Spec Data Sheet after the charts and
  an enabled independent air-piping page immediately after it. Ordinary Accessory Sheet,
  positioner/filter-regulator/solenoid/restrictor sheets, calculations, quality records,
  certificates, outlines, and unrelated attachments remain excluded unless the ordinary
  accessory feature is explicitly enabled.
- `include_air_piping_pages` controls only air-piping pages. `include_accessory_pages`
  controls only ordinary Accessory Sheet pages and defaults to false. A legacy config
  containing only the latter must not silently treat ordinary accessories as air-piping
  pages; emit an internal migration notice.
- Accessory metadata is extracted regardless of `include_accessory_pages`. The matched
  Specification/Accessory full positioner order model is the display model in Stage 2
  approval and formal customer reports; the current DTM/Sequencer device family and
  revision remain separate identity evidence. A user-confirmed replacement status is
  a project fact included in the Stage 2 approval fingerprint and UI only. Formal
  customer DOCX/PDF generation must not read or display that replacement annotation.
- Before rendering, merge page roles by physical identity
  `source_sha256 + one_based_page_number`. A multi-role physical page satisfies every
  role-coverage check but produces one image and one DOCX page. Never merge different
  physical pages by title or image similarity, and never create an empty section for a
  missing role.
- A Specification, air-piping, or accessory page whose role or valve assignment
  is not reliable must be visually reviewed once with `codex exec` after automatic
  classification. A page is not reviewed merely because it is new when its
  automatic assignment is reliable. This is a page-role observation, not a chart
  observation or diagnosis; it must not produce a rating, fault conclusion, or
  action. A batch of uncertain pages may be reviewed in one invocation.
- Extract per-valve time from raw Start/End Time. DTM/Sequencer Device Type is the
  observed device family/version; use reliably matched technical-page order models
  for the full positioner model, as required above.
- Diagnostic reports require a validated interpretation for every inserted chart
  from the integrated model call or a properly registered human return. Do not ship
  `待复核` placeholders as completed interpretations. Pure-data reports retain their
  separate contract and do not add diagnostic prose.
- Preserve non-success results from the source Task Execution Summary. Mark Failed, Cancelled,
  Aborted, Incomplete, or Not Completed tasks in the corresponding valve chapter.
- Use one chart order across every valve: Run Step, Pattern Step, Run Ramp, Standard Signature,
  Extended Actuator Signature, then other charts; within a type sort by source and numeric page.
- Preserve physical PDF page boundaries while classifying charts. Specific source titles and
  `Step Type` semantics override generic customer-layout captions such as `Run Signature Test Status`.
  Count one cross-page physical test once, retain multiple independent tests on one page, and never
  treat a cover parameter block or result table without a plot as a diagnostic chart.
- `--extract-materials` prepares project PDF text/OCR as part of evidence
  extraction. Use native PDF text when available and OCR only unreadable pages.
  Cache by content hash and page; report generation must never invoke OCR.
  Use `--prepare-ocr` alone only for compatibility or troubleshooting.

## Output rules

- After Stage 3 approval and a valid frozen run, Stage 4 generates one review
  DOCX and runs the single required-information acceptance. A rated
  content-confirmation DOCX includes frozen conclusions by default. An explicit
  `blank_conclusions = true` requests a blank draft, which cannot satisfy Stage 5. Submit
  the actual DOCX and compact four-count result; do not create another long
  internal review report for the user.
- Stage 5 follows [authorization and report status](#authorization-and-report-status):
  retain confirmation of the named DOCX or explicit delegation to the Agent, its
  verbatim instruction and actual version in the existing task record; no extra
  approval CLI. Route a correction to its owner:
  parameters to the affected Stage 2 valve, objective observation to the exact
  image, diagnosis to an affected-valve child run, report mapping to the shared
  report layer, and layout to a format-only child. Do not restart unrelated
  stages.
- Stage 6 exports the confirmed DOCX with WPS, performs the automatic delivery
  check and representative-page visual review, and registers the DOCX/PDF only
  after `wps_delivery_validated`. Do not repeat checks for the same unchanged
  artifact; after a fix, rerun the affected validation and the registration
  command's required checks. Stage 7 runs only when customer feedback arrives.
- After Stage 2, the only user confirmations are the compact Stage 3 diagnosis
  review and the Stage 5 DOCX content review. Freeze verification, hashes,
  automatic report checks and contact-sheet review are internal checks, not
  separate user gates.
- Default order: rating standard and summary, overall conclusion and recommendations, then per-valve sections.
- The overall conclusion is a concise batch summary, not a concatenation of
  per-valve conclusions. Without a verified project summary, report the final
  rating counts and point to the tagged priority list and per-valve chapters.
  Keep valve-specific actions in those sections; do not repeat every action
  without its valve tag as a batch recommendation.
- New projects and projects without explicit presentation settings default the four
  overview blocks `include_field_context_overview`, `include_overview_judgement`,
  `include_overview_priority`, and `include_retest_recommendations` to false. These
  switches do not remove the rating summary, the main overall-conclusion chapter,
  priority action list, or per-valve operating context.
  `recommendation_overrides` supplies text but does not enable its section:
  `include_retest_recommendations=true` is also needed to display the standalone
  summary recommendations. Verify the requested section in the rendered report.
- Missing description, unit, failure consequence, bypass, and field notes render as
  `待业主补充`; unit must never fall back to customer name. When Sequencer is the
  current source and no DTM/Active Report exists, use `运行与定位器诊断信息`, show the
  complete start/end time window, and normalize mixed action text such as `Double / 气开`
  to `双作用 / 气开` (with equivalent single-action/air-close forms).
- A per-valve Sequencer chapter displays the current positioner/configuration snapshot,
  strokes, cycles, open/closed/near-closed hours, active and historical faults as
  separate groups, every non-success task, and separate max/average/min rows for Run
  Ramp hysteresis, dead band, and linearity. Customer text must not expose JSON paths,
  source filenames, cache state, Markdown status, or freeze implementation details.
- Preserve 1–5 health levels and evidence limits.
- When previously human-confirmed DOCX reports or Agent-verified derivatives of a human return need only a new unit grouping, `--assemble-units <project/work/selection.json>` may compose their complete per-valve chapters without claiming a new diagnosis or frozen run. The selection binds archived source hashes, provenance, period valve scopes and the project unit map. The assembler recalculates unit counts and grades from the selected chapters, preserves their text and images, removes unused source images, and rejects missing or duplicate valves. Keep the archived sources immutable; any diagnostic rewrite returns to the frozen-run route above. Remove internal per-valve review-state paragraphs from a versioned delivery source before assembly; customer DOCX/PDF must not contain `人工校验状态` or `已纳入人工核对意见`.
- Unit assembly keeps one clean footer reference when source reports carry overlapping first/even-page footers; this changes no valve chapter text or media. New output versions advance beyond the highest existing unit-volume version.
- Keep internal acquisition, paths, filenames, feedback provenance, and processing traces out of customer DOCX/PDF.
- Store LibreOffice-rendered PDFs only under `work/pdf-checks/`. They are internal content-check artifacts and must
  never be registered or sent as delivery PDFs.
- A delivery PDF must be exported by WPS from the corresponding DOCX, use the same directory and stem, and pass WPS
  creator, A4 portrait, blank-page, rotation, complete device-overview, content, curve, and specification checks.
- Generate a representative WPS contact sheet and actually inspect the cover, overview, first/middle/last valve
  pages, chart pages, and Specification/air-piping pages (plus ordinary accessory pages
  only when enabled). Record the review note before setting
  `wps_delivery_validated`.
- Debug files such as `.wps-rendered.pdf`, contact sheets, and layout text dumps are not delivery artifacts.
- A new WPS validation records its input hashes and clears the current success
  status before checking. Failures remain explicit; prior manifests are retained
  under the report's validation `history/`, with their contact sheets preserved.
- Write the current work manifest to `work/run_manifest.json` with engine/config/input/output hashes; the loaded experience is bound inside the frozen diagnosis.
- Diagnostic review and business reports require `paths.formal_diagnosis_run` to
  name one valid frozen project run. They read final ratings, conclusions, issues,
  actions and curve interpretations only from that run, then apply only registered
  human fields bound to the review base and the same frozen-run identity. A configured
  `diagnostic_summary_overrides` or manual curve file without that binding is rejected;
  project experience and old summaries are not fallback diagnosis sources.
- Every merged per-valve result must contain an integer 1–5 rating, non-empty rating
  conclusion and main issue, an issues list (which may be empty), and non-empty actions.
  An omitted human field inherits the frozen field; an explicitly empty required field
  is invalid. Default overall text reports final rating counts and points to the
  tagged details; it does not concatenate per-valve prose or all actions. A project
  summary may restate common findings and priorities only from the same merged
  diagnosis version, with its source/version in the task record. Honor registered
  project-level human text. Report generation must not rerun rating or add root
  causes, thresholds, or actions absent from the source conclusions.
- The Stage 3 algorithm audit snapshot is read-only report provenance. A report may
  display its frozen data/visual/combined audit values, but must not recompute them or
  use them in place of the merged final diagnosis. A complete legacy frozen package
  without a snapshot remains readable with `algorithm_audit_status=legacy_unavailable`
  and null audit values; do not backfill, default to Level 5, migrate, or edit that package.
- Generate the review report with the same business-dashboard tables as the formal report.
  Preserve frozen final fields, frozen algorithm-audit values when available, and non-success
  test execution results for human validation. Review and ordinary report generation
  both default `blank_conclusions` to false; only an explicit true produces a blank
  draft. A blank draft is not a completed report. Archive the returned
  DOCX, persist its content-only diff, and generate a business report only after an explicit
  user instruction.
- Generate data-only reports under `output/data/` with independent versioning for each report period and snapshots
  under `work/data/<period>/vN/`. This route must not load a frozen diagnosis, human
  diagnosis/curve overlay, algorithm audit snapshot, project experience rating, or rating
  default; unknown-rating defaults must not affect valve selection. Preserve factual device/field information, original charts, measured metrics,
  non-success test execution results, and reliably matched enabled Specification/air-piping/accessory pages. Do not display ratings,
  health status, diagnostic conclusions, issue judgments, actions, or curve-interpretation body text.
- Never overwrite report versions or automatically delete historical OCR, snapshots, returned files, change lists, or reports.
- Once report content is confirmed, formatting remediation must use a format-only
  child version. Do not use a report generation command merely to obtain a new
  version number. Only layout properties may change; text, table values, media,
  bookmarks, links, and embedded objects must remain content-identical to the
  confirmed parent.

## Single required-information acceptance

The default customer-report gate is one compact, outcome-based check. Run it through the
shared entry point with `--check-report-information <DOCX>` and optionally
`--check-report-pdf <PDF>`. It confirms only that:

- every included valve chapter is present;
- the required operating, fault, task and Run Ramp fields appear with values bound to
  the current report summary;
- every declared curve/interpretation and enabled Specification/air-piping physical page
  reached the document exactly once;
- disabled overview blocks, ordinary accessory/restrictor pages and internal processing
  text are absent; and
- the supplied DOCX/PDF can be read.

The command returns four compact confirmed/expected counts and writes full details to
`work/information-acceptance/<report-stem>.json`. Repeated per-field failures are grouped
in terminal/chat output. Hash, bookmark, inheritance and implementation diagnostics stay
available for explicit developer troubleshooting, but they are not separate default
customer-facing gates; they block only when they establish a missing, mismatched,
duplicated, unreadable or internally contaminated customer deliverable. During
development run focused tests, then run the portable full suite once after the shared
change is stable. A report-only acceptance failure must not trigger OCR, visual review or
formal diagnosis again.

## Report quality checks

The four completeness counts prove coverage, not readable prose, sound aggregation,
or human calibration. Within the existing Agent acceptance step also check:

- Summary: correct batch and rating counts, concise batch-level findings and data
  limits, traceable priorities; no unlabelled concatenation of valve conclusions.
- Recommendations: common actions summarized without losing conditions; valve-specific
  actions remain attached to their tags. Confirm the configured section is visible.
- Per-valve prose: explain the problem, functional impact and next action in ordinary
  sentences. Keep decision-relevant numbers and conditions, with full metrics in the
  evidence and data tables. Do not turn each curve or parameter into a separate issue,
  omit relevant operating history, or rewrite frozen conclusions during rendering.
- Version: per-valve ratings and conclusions match the frozen run / registered return.
  Summary-only edits create a new content version and leave per-valve text and media
  unchanged; they are not format-only edits or a new diagnosis.
- Layout: readable cover/title, no stranded title character, complete tables, no
  clipping or overlap, complete overviews, curves and technical-page representatives.

Record these results in the existing task / delivery record, without an extra user
gate. Customer-facing counts remain compact. `information_complete` and
`wps_delivery_validated` describe their respective checks, not engineering expert sign-off.

Visual checks must target the current explicit PDF and images. A layout reviewer
must not search the project for other versions or run the production workflow. For
an alleged defect, save the exact page/render and verify it against the current PDF;
resolve contradictory reviews using those pages and, when useful, a second renderer.
Neither a single model PASS nor FAIL overrides contrary evidence. After a format
fix, reuse observations only for verified identical renders/contact sheets, check
changed pages and the final representative sheet, and retain the identity comparison.
