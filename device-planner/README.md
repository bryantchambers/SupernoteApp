# Supernote Planner device plugin

Companion to `/planner/` in this repository; not a separate calendar service.
Target: A6X, Chauvet **2.27.42**, React Native **0.79.2**, React **19.0.0**,
`sn-plugin-lib` **0.2.3**, official scaffold **1.0.13**.

## Build status

The platform-independent rules run with `node --test device-planner/core.test.mjs`.
The Android package was built on `rockpigeon` in `~/Projects/SupernotePlugin`.
Its native bridge registration and JavaScript bundle contents were verified.
Firmware APIs still require real-device acceptance.
Do not mark the device validated based on the JavaScript tests alone.

The orchestrator must provision JDK 17, the Android SDK and the official scaffold's
Gradle/SDK requirements in an isolated build workspace. No host package install is needed.
Use the official template 1.0.13 with RN 0.79.2, then pin the SDK to 0.2.3 and retain
the generated lockfile. Apply our source with:

```sh
python device-planner/prepare.py /path/to/SupernotePlanner
cd /path/to/SupernotePlanner
./buildPlugin.sh
```

The isolated build configuration has user approval. On an x86_64 Docker builder:

```sh
docker build --platform linux/amd64 -t supernote-planner-builder device-planner
mkdir -p /tmp/planner-plugin-output
docker run --rm -v /tmp/planner-plugin-output:/outputs supernote-planner-builder
```

The ARM64 app server uses the artifact from the approved x86_64 builder; no host
emulation or host package changes were needed. Planner settings offer authenticated
downloads of `SupernotePlanner.snplg` and the public CA certificate.

Build **with the native module** (`app.npk`), not only a JavaScript bundle: encrypted
storage and HTTPS are implemented by `PlannerStoragePackage`. Verify the native
package includes `com.supernoteplanner.PlannerStoragePackage` before installation.
Keep `pluginID` and `pluginKey` stable across updates. The bridge intentionally
accepts only its SDK-provided private directory; verify that path on the device.

## Set up

1. Back up the device, update firmware, and copy the built `.snplg` into MyStyle.
2. Install via Settings → Apps → Plugins. Grant declared FILE:READ, FILE:WRITE,
   INTERNET permissions when requested. Permission handling must be validated on firmware.
3. Generate/download the PDF in the web app. Copy it to MyStyle, apply **all PDF
   pages in order** as a new standard notebook's templates; save as
   `Note/Planner/2027.note`. The PDF is an immutable background, not the live calendar.
4. With plugin **0.1.4 or newer**, click **Download pairing file** in web planner
   settings. Copy `supernote-planner-pairing.json` into `Document` on the tablet.
   Keep that exact filename. In the plugin tap **Import pairing file from Document**.
   It reads the named file directly, without a file chooser or copy callback. This fills and saves
   all connection details, including the public CA certificate. No private key
   or Google secret goes on A6X. Remove the pairing file from the tablet and any
   cloud copies after importing: its device credential grants planner access.
   Manual connection fields remain available under **Manual setup**.
   Version 0.1.4 stores encrypted state separately from installation files, migrating
   any existing state without resetting its queue. Re-import is unnecessary
   when the app address is already displayed. Read-only bridge calls time out with
   the failing step named; late read results cannot overwrite the connection.
5. Open the new notebook. Save connection, initialize, then sync. Initialization
   is resumable but must precede page reordering. Never initialize an unrelated note.
6. Run every hardware acceptance case in `docs/PLANNER.md` before enabling writes.

## Pen contract

Write one entry per ruled capture row: `14:00–15:00 Dentist`, `All day: Birthday`,
or `□ Buy milk`. Optional `@alias` chooses a configured destination. Other writing
is reviewed and never silently published. Calendar rows are separate from capture ink.
Cancel a generated calendar row with one nearly horizontal line spanning at least
70% of that row; ordinary X marks and erasure do not trigger deletion. Ambiguous
cancellations need explicit correction in the web app.

State/outbox are encrypted with Android Keystore and saved atomically before requests.
Clear data/uninstall can remove the queue and key: sync before uninstalling.
Plugin **0.1.8** fills each day's available vertical space at the existing readable
font size and row spacing, rather than stopping after two entries. The current
template fits up to five rows in a blank day box. Rows stop before the next day,
weekly notes or page navigation and skip existing pen strokes. Existing events
keep their row positions, so cancellation marks cannot attach to another event.
The remaining overflow count appears only on the final visible row; all entries
remain available in the app. Caption and position changes trigger a redraw even
when event versions have not changed. Cross-outs/checks on expanded rows are
processed before capture OCR. Existing notebooks and PDFs need no regeneration;
install the update and sync. The storage safeguards introduced in 0.1.7 remain.

## Hardware checks still required

Plugin **0.1.7** changes encrypted state storage to a versioned, gzip-compressed
envelope of independently authenticated blocks, each at most 16 KiB. Every block
has a fresh Keystore nonce; write identity, sizes, count and block index are
authenticated to reject truncation, reordering and mixed writes. Whole-state
Android Keystore operations are avoided. Legacy IV+ciphertext is still read,
using bounded update calls before tag verification. A valid legacy state is
migrated on the next ordinary save; its old encrypted bytes are retained.

Before replacing active state, every save now decrypts its candidate in memory,
writes a separate staged file and verifies that file through the on-device
provider. Failure keeps the active queue intact. Recovery still requires explicit
confirmation and matching notebook anchors and activates a fresh generation only
after verification. Previous generations and keys are never deleted. A native
codec-version check rejects a new JS bundle running with the old native module.

Install the full 0.1.7 package **over the existing plugin**, restart the tablet,
open the original notebook and try **Sync Planner** first. If legacy state is
readable with bounded updates, its queue is preserved automatically. Only if the
authentication error remains, use the existing pairing file and confirmed recovery;
unreadable unsynced state still cannot be promised recoverable. Do not clear app
data, uninstall or initialize a replacement notebook.

The recovery error after adding Exchange coincided with a minimum 174,521-byte
manifest/items state (180 pages, 252 items). A size-sensitive Keystore/provider
failure is a working hypothesis, not a verified hardware diagnosis. The portable
codec has real JCE round-trip and adversarial tests; actual Android Keystore and
firmware behavior still require the A6X acceptance check. Native errors now name
the failed encryption, verification, staged-read or commit step.

The isolated Android Docker build runs the dependency-free Java tests before SDK
contract tests and packaging. To run just the codec suite with an existing JDK:

```sh
javac -encoding UTF-8 --release 8 -d /tmp/planner-storage-tests device-planner/native/PlannerStateCodec.java device-planner/native-tests/PlannerStateCodecTest.java
java -cp /tmp/planner-storage-tests com.supernoteplanner.PlannerStateCodecTest
```

Plugin 0.1.6 treats screen-awake control as optional: a headless host must not
block pairing/sync because it exposes no Activity. A visible warning asks the
user to keep the tablet awake or increase its auto-sleep interval.

For a confirmed `AEADBadTagException`, **Review connection recovery** offers a
second explicit confirmation. Put the original pairing JSON in Document and
open the original initialized notebook first. Recovery verifies the server,
page count and every page identity before creating a new encrypted generation
with its own key. It verifies a read-back before atomically activating that
generation; old files and keys are never deleted or overwritten. It cannot
restore unsynced changes from the unreadable generation. It keeps server data
and notebook ink, re-queues previews and sends re-scanned captures to review
to catch duplicates. Do not reinitialize afterward; use Sync Planner.
Native Keystore behavior and the exact cause of the authentication failure
still require verification on hardware.

Plugin 0.1.5 adds a fixed **Return to notebook** button (also Android Back).
During initialization/sync it requests a safe stop and closes after the current
operation is saved. Backgrounding requests a pause; foreground work requests
Android's screen-on window flag, restored when the action finishes. Explicit
sleep can still interrupt native work. Resume with **Sync Planner**, without
reinitializing or re-pairing. Completed previews are checkpointed individually;
an interrupted upload can be retried safely. Native errors now include their
exception class instead of only "Planner action failed". If saved-state loading
fails, restart the tablet first and keep the error text; never clear plugin data
or overwrite the encrypted queue as a recovery shortcut.

Plugin 0.1.2 keeps progress visible above the scrolling controls, explicitly
refreshes the e-ink plugin view, and shows a native result dialog. The last result
is retained in encrypted plugin storage. **Check notebook (read only)** reports
the open file, actual/expected page counts and initialization state without saving
or changing the notebook. Run it first when initialization or sync appears silent.

Engine guard checks run with:

```sh
node --experimental-vm-modules --test device-planner/core.test.mjs device-planner/engine.test.mjs
```

Plugin 0.1.3 corrects SDK compatibility for text/stroke constants, integer text
rectangles, native cache recycling and false native mutation responses. Every
Android build now also runs `sdk-contract.test.mjs` against the installed SDK
0.2.3 source, including its actual exports, validators and point accessors. Only
the Android bridge and server transport are simulated. These checks exercise
180-page initialization/resumption, handwriting capture, managed text replacement,
preview upload and connection-loss retry while preserving ink and PDF links.
See `docs/PLANNER_PLUGIN_REVIEW.md` for findings and hardware limits.

Anchor userData persistence; SDK private directory; main-layer TextBox validation;
file-level edits vs open editor state; save/reload behavior; permission prompts;
full-year memory/latency; PNG export behavior; page reorder/copy handling; encrypted
state across restart/upgrade; private-CA HTTPS; ordinary handwriting false positives.

Official reference: <https://docs.supernote.com/en>.
