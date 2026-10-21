# Planner plugin SDK review — 21 October 2026

Trigger: A6X initialization stopped on page 1 with `TYPE_TEXT` undefined.
Reviewed against the npm `sn-plugin-lib` 0.2.3 source used by the approved builder.

## Corrections in 0.1.3

| Finding | Correction |
| --- | --- |
| `ElementType` exists internally but is not a package-root export. | Use exported `Element.TYPE_TEXT` and `Element.TYPE_STROKE`. |
| Native-created/read elements are plain bridge objects; SDK transformation does not install `Element.prototype`. | Recycle with `PluginCommAPI.recycleElement(uuid)`, including existing anchors and exception paths. |
| SDK `RectSchema` requires integer coordinates. | Round and bound text rectangles to page pixels, require nonzero area and a valid page size. |
| Text boxes were manually assembled with inconsistent defaults. | Use the exported `TextBox` class and its documented editable default. |
| A successful response envelope may still contain `result: false` for a failed mutation. | Require confirmed `true` for saves, inserts, deletes, reloads and PNG generation. |
| Stroke size/point reads could pass invalid values into grouping/OCR. | Validate point counts, completeness and finite coordinates; bound per-stroke and per-page memory. |
| First sync rendered previews for every untouched blank weekly page. | Generate previews for ink, changed content, managed text and pending retries. |
| Last-chunk receipt counts could underreport pending reviews. | Report the server's total review/conflict count, including the final acknowledgement. |
| Network response limits were checked after allocating the complete response. | Bound streaming reads and check preview file size before allocation; reject URL fragments. |

Also checked: all imported SDK exports and called methods; recognition and point
accessor arguments; file insertion/deletion indices; page anchors and ownership;
pairing import; encrypted atomic outbox persistence; retry identities; public-CA
verification; origin restrictions; and plugin identity/version handling.

## Regression coverage

`device-planner/sdk-contract.test.mjs` loads the real pinned SDK exports, parameter
validators, model classes and point accessors. It deliberately returns plain
objects from the simulated Android bridge. Native file mutations are simulated;
these tests do not prove real firmware behavior.

The five scenarios cover:

- 180-page initialization and repeat initialization without duplicate anchors/cache leaks.
- Handwriting recognition, managed calendar text, preview generation and final acknowledgement.
- Managed text replacement preserving handwritten strokes and pre-existing PDF links.
- Connection loss after server receipt, retaining the outbox and retrying without duplicate capture.
- Invalid native point reads and a failed insertion; safe stop and resumption without duplicate anchors.

These checks run inside the Docker builder before packaging. The existing core
and engine tests additionally cover page identity, explicit cancellation, blocked
ink rows, pairing validation, permissions and read-only notebook inspection.

To run with an existing SDK checkout/package:

```sh
SN_PLUGIN_SDK_ROOT=/path/to/sn-plugin-lib node --experimental-vm-modules --test \
  device-planner/core.test.mjs device-planner/engine.test.mjs \
  device-planner/sdk-contract.test.mjs
```

## Remaining hardware checks

The A6X has confirmed pairing/network access and reading the 180-page notebook.
Text insertion, metadata persistence through save/reload/cloud sync, actual OCR,
PNG export and handwritten cancellation still require the real-device acceptance
sequence in PLANNER.md. Google writes remain disabled. SDK compatibility tests
reduce repeated API-shape failures; they cannot guarantee firmware behavior.

## Pairing callback follow-up — 0.1.4

The device subsequently reported a pairing import waiting more than 140 seconds.
The previous import path did not report substeps and could wait indefinitely on
host file-selector/copy promises. The SDK contract checks simulated native calls;
they did not prove Android picker lifecycle behavior.

The normal import now reads only `Document/supernote-planner-pairing.json` through
the plugin's native bridge, without picker/copy callbacks. Each import step is
reported. Read-only bridge operations have deadlines; a late read result cannot
continue into saving a different connection. Writes are not raced against timers.
Invalid/missing input, timeout and pending-queue preservation have regression
coverage. All core, engine and SDK contract tests now run inside the Android
builder. The device still needs to verify this direct filesystem import path.

Encrypted connection state and pending operations now live in a private data
directory separate from the plugin installation. Existing encrypted state is
migrated atomically with the same Keystore key when it is still present; upgrades
cannot recover state that an earlier installation already removed.

## Sleep and saved-state follow-up — 0.1.5

The user reported sleep during preview upload 44 and a subsequent saved-state
load failure. The server retained 43 previews. The underlying storage exception
is not yet identified: the old error handler hid exception classes when messages
were null. It also caught bridge delivery errors and could attempt to reject an
already settled callback. Native dispatch now settles once, and reports exception
class/cause. Reading or saving an existing encrypted state never generates a
replacement key when its alias is unavailable; no reset/fallback discards queued
operations.

Foreground actions request FLAG_KEEP_SCREEN_ON and release it afterward.
A fixed close button and Android Back request cooperative stop at page/operation
boundaries; backgrounding also requests a pause. Capture/preview intent is saved
before advancing, and each successful upload removes only its own pending entry.
SDK contract tests cover failure at upload 44, resume without resending the first
43, stopping during uploads and pausing after handwriting recognition. The tablet
must still verify screen-on behavior, close lifecycle and the actual storage error.

## Headless host and confirmed authentication failure — 0.1.6

The tablet exposes no foreground Activity to this native module. This made the
0.1.5 awake guard a new blocker before import/sync even began. Awake control now
returns unavailable instead of rejecting, and JS treats missing/rejected window
control as optional, with a visible manual-sleep warning.

The user confirmed `AEADBadTagException`. Its root cause remains unproven; no
code assumes that re-pairing can decrypt existing ciphertext. Explicit recovery
checks the original notebook anchors against the pairing file and server before
any local recovery write. Native recovery independently requires an actual
AEAD authentication failure. A fresh encrypted file/key is read-back verified,
then an atomic generation marker selects it. Prior ciphertext and keys remain
untouched. The UI explains that unreadable queued changes cannot be restored;
re-scanned handwriting is forced into review. No notebook mutation, server item
deletion or Google-write enablement occurs during recovery.

SDK tests cover optional awake failures, readable-state refusal, wrong notebook
refusal, successful identity verification, untouched ink and review flags.
Android Keystore and generation activation still need a real-device check.
