package com.supernoteplanner

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.os.Environment
import android.view.WindowManager
import android.util.AtomicFile
import android.util.Base64
import com.facebook.react.bridge.*
import java.io.File
import java.net.URL
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.util.UUID
import java.util.concurrent.Executors
import org.json.JSONObject
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory

/** Private, encrypted, atomic state and origin-scoped HTTPS; no external dependencies. */
class PlannerStorageModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
    private val executor = Executors.newSingleThreadExecutor()
    private val keyAlias = "SupernotePlanner-v1"
    override fun getName() = "PlannerStorage"

    @ReactMethod fun getStateCodecVersion(promise: Promise) { promise.resolve(2) }

    @ReactMethod fun setKeepAwake(enabled: Boolean, promise: Promise) {
        val activity = context.currentActivity
        if (activity == null) { promise.resolve(false); return }
        activity.runOnUiThread {
            try {
                if (enabled) activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                else activity.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            } catch (e: Exception) { promise.reject("PLANNER_WINDOW", e.javaClass.simpleName); return@runOnUiThread }
            promise.resolve(true)
        }
    }

    @ReactMethod fun readPairingFromDocument(promise: Promise) = execute(promise) {
        // Read only the explicitly named pairing file; no picker callback, file scan or arbitrary path.
        val storageRoot = Environment.getExternalStorageDirectory().canonicalFile
        val document = File(storageRoot, "Document").canonicalFile
        val file = File(document, "supernote-planner-pairing.json").canonicalFile
        require(document.parentFile == storageRoot && file.parentFile == document) { "Invalid pairing file location" }
        require(file.isFile) { "Copy supernote-planner-pairing.json into the tablet's Document folder, then import again" }
        require(file.length() in 1L..65536L) { "Pairing file is empty or too large; download a fresh pairing file" }
        file.inputStream().use { String(readBounded(it, 65536), Charsets.UTF_8) }
    }

    @ReactMethod fun readPairingFile(root: String, path: String, promise: Promise) = execute(promise) {
        val file = privateFile(root, path)
        require(file.name == "pairing-import.json") { "Invalid pairing import path" }
        try {
            require(file.length() in 1..65536) { "Pairing file is empty or too large" }
            file.readText(Charsets.UTF_8)
        } finally { file.delete() }
    }

    private fun folder(path: String): File {
        val file = File(path).canonicalFile
        val permitted = File(context.filesDir, "plugins/snp2027a6x000001").canonicalFile
        require(file == permitted) { "Invalid plugin private directory" }
        require(file.exists() || file.mkdirs()) { "Cannot create plugin private directory" }
        return file
    }
    private fun privateFile(root: String, path: String): File {
        val file = File(path).canonicalFile
        require(file.path.startsWith(folder(root).path + File.separator)) { "File is outside this plugin" }
        return file
    }
    private fun stateFile(root: String): AtomicFile {
        val installation = folder(root) // Validate the SDK-provided plugin identity.
        val persistent = File(context.filesDir, "planner-data/snp2027a6x000001").canonicalFile
        require(persistent.exists() || persistent.mkdirs()) { "Cannot create persistent planner storage" }
        val target = AtomicFile(File(persistent, "state.enc"))
        if (!target.baseFile.exists() && !File(target.baseFile.path + ".bak").exists()) {
            val legacy = AtomicFile(File(installation, "state.enc"))
            if (legacy.baseFile.exists() || File(legacy.baseFile.path + ".bak").exists()) {
                // Preserve the encrypted bytes and existing key; never reset a pending outbox on upgrade.
                val previous = legacy.readFully()
                val stream = target.startWrite()
                try { stream.write(previous); target.finishWrite(stream) }
                catch (e: Exception) { target.failWrite(stream); throw e }
            }
        }
        return target
    }
    private fun stateKey(alias: String, create: Boolean): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        require(create) { "Saved planner encryption key is unavailable. Restart the tablet and retry. Keep plugin data intact; do not re-pair or clear storage." }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    private fun location(root: String): Pair<AtomicFile, String> {
        val legacy = stateFile(root)
        val marker = AtomicFile(File(legacy.baseFile.parentFile, "active-state.json"))
        if (!marker.baseFile.exists() && !File(marker.baseFile.path + ".bak").exists()) return Pair(legacy, keyAlias)
        val id = JSONObject(String(marker.readFully(), Charsets.UTF_8)).getString("generation")
        require(UUID.fromString(id).toString() == id) { "Invalid planner recovery generation; keep storage intact" }
        val file = AtomicFile(File(legacy.baseFile.parentFile, "state-$id.enc"))
        require(file.baseFile.exists() || File(file.baseFile.path + ".bak").exists()) { "Recovered planner state is missing; keep storage intact" }
        return Pair(file, "$keyAlias-$id")
    }
    private fun decrypt(location: Pair<AtomicFile, String>): String {
        val bytes = location.first.readFully()
        return String(PlannerStateCodec.decrypt(bytes, stateKey(location.second, false)), Charsets.UTF_8)
    }
    private fun writeAtomic(file: AtomicFile, bytes: ByteArray) {
        val stream = file.startWrite()
        try { stream.write(bytes); file.finishWrite(stream) }
        catch (e: Exception) { file.failWrite(stream); throw e }
    }
    private fun verifiedSave(selected: Pair<AtomicFile, String>, value: String, create: Boolean) {
        val key = storageStage("Opening the state key") { stateKey(selected.second, create) }
        val encoded = storageStage("Encrypting bounded planner blocks") { PlannerStateCodec.encrypt(value.toByteArray(Charsets.UTF_8), key) }
        // Check the on-device provider BEFORE touching the active queue.
        require(storageStage("Verifying encrypted blocks before saving") { String(PlannerStateCodec.decrypt(encoded, key), Charsets.UTF_8) } == value) {
            "Planner encryption verification failed; the previous saved queue is preserved"
        }
        val staged = AtomicFile(File(selected.first.baseFile.parentFile, "staged-${UUID.randomUUID()}.enc"))
        storageStage("Writing the staged planner state") { writeAtomic(staged, encoded) }
        require(storageStage("Reading back the staged planner state") { decrypt(Pair(staged, selected.second)) } == value) {
            "Planner read-back verification failed; the previous saved queue is preserved"
        }
        // Keep the previous encrypted bytes even after a successful format upgrade.
        if (selected.first.baseFile.exists() || File(selected.first.baseFile.path + ".bak").exists()) {
            val previous = selected.first.readFully()
            if (!PlannerStateCodec.isCurrentFormat(previous)) {
                val retained = AtomicFile(File(selected.first.baseFile.parentFile, "retained-before-v2-${selected.first.baseFile.name}"))
                if (!retained.baseFile.exists()) storageStage("Retaining the old encrypted format") { writeAtomic(retained, previous) }
            }
        }
        storageStage("Committing verified planner state") { writeAtomic(selected.first, encoded) }
        // The staging file has served its purpose; old states and keys are kept.
        staged.delete()
    }
    private fun <T> storageStage(stage: String, action: () -> T): T {
        try { return action() }
        catch (e: Exception) { throw IllegalStateException("$stage failed (${e.javaClass.simpleName}); previous saved state was kept", e) }
    }
    private fun execute(promise: Promise, action: () -> Any?) {
        executor.execute task@ {
            // Settle once. A bridge callback failure must never cause a second callback.
            val result = try { action() } catch (e: Exception) {
                val cause = e.cause?.javaClass?.simpleName
                val detail = e.javaClass.simpleName + (if (cause != null) " ($cause)" else "")
                promise.reject("PLANNER", "$detail: ${e.message ?: "Native operation failed"}. Saved planner files have not been cleared.")
                return@task
            }
            promise.resolve(result)
        }
    }
    private fun readBounded(stream: java.io.InputStream, maximum: Int): ByteArray {
        val output = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (true) {
            val count = stream.read(buffer)
            if (count < 0) break
            require(output.size() + count <= maximum) { "Server response is too large" }
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }
    @ReactMethod fun load(root: String, promise: Promise) = execute(promise) {
        val selected = location(root)
        val file = selected.first
        if (!file.baseFile.exists() && !File(file.baseFile.path + ".bak").exists()) "{}" else decrypt(selected)
    }
    @ReactMethod fun save(root: String, value: String, promise: Promise) = execute(promise) {
        val selected = location(root)
        val file = selected.first
        val existing = file.baseFile.exists() || File(file.baseFile.path + ".bak").exists()
        // Never create a replacement key over an existing queue or overwrite an
        // unreadable state through an ordinary import/save action.
        if (existing) decrypt(selected)
        verifiedSave(selected, value, !existing)
        true
    }
    @ReactMethod fun recover(root: String, value: String, promise: Promise) = execute(promise) {
        val previous = location(root)
        var failedAuthentication = false
        try { decrypt(previous) } catch (e: AEADBadTagException) { failedAuthentication = true }
        require(failedAuthentication) { "Recovery is only available for an authenticated-state read failure; existing readable state is preserved" }
        val state = JSONObject(value)
        require(state.getJSONArray("outbox").length() == 0 && state.getBoolean("recovered")) { "Invalid recovery state" }
        val id = UUID.randomUUID().toString()
        val fresh = Pair(AtomicFile(File(previous.first.baseFile.parentFile, "state-$id.enc")), "$keyAlias-$id")
        verifiedSave(fresh, value, true)
        require(decrypt(fresh) == value) { "Recovery verification failed; original planner state is preserved" }
        // Activate only after a verified write. Keep every prior encrypted file and key.
        val marker = AtomicFile(File(previous.first.baseFile.parentFile, "active-state.json"))
        writeAtomic(marker, JSONObject().put("generation", id).toString().toByteArray(Charsets.UTF_8))
        true
    }
    @ReactMethod fun hash(value: String, promise: Promise) = execute(promise) {
        MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it.toInt() and 255) }
    }
    @ReactMethod fun randomId(promise: Promise) = execute(promise) { UUID.randomUUID().toString() }

    @ReactMethod fun request(root: String, origin: String, endpoint: String, token: String, body: String, caPem: String,
                             pngPath: String, pageId: String, revision: Double, promise: Promise) = execute(promise) {
        val base = URL(origin)
        require(base.protocol == "https" && base.userInfo == null && base.query == null && base.ref == null && (base.path == "" || base.path == "/")) { "Use your app's HTTPS origin" }
        require(endpoint == "/planner/api/v1/sync/" || endpoint == "/planner/api/v1/preview/") { "Endpoint is outside the planner API" }
        val connection = URL(origin.trimEnd('/') + endpoint).openConnection() as HttpsURLConnection
        if (caPem.isNotBlank()) {
            val certificate = CertificateFactory.getInstance("X.509").generateCertificate(caPem.byteInputStream())
            val trustStore = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null); setCertificateEntry("planner-ca", certificate) }
            val managers = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(trustStore) }
            connection.sslSocketFactory = SSLContext.getInstance("TLS").apply { init(null, managers.trustManagers, null) }.socketFactory
        }
        // Preserve hostname verification; never follow a redirect with device credentials.
        connection.instanceFollowRedirects = false
        connection.connectTimeout = 15000
        connection.readTimeout = 30000
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("Authorization", "Bearer $token")
        val bytes = if (pngPath.isBlank()) body.toByteArray(Charsets.UTF_8) else {
            val file = privateFile(root, pngPath)
            require(file.length() <= 5 * 1024 * 1024) { "Request is too large" }
            file.readBytes()
        }
        require(bytes.size <= 5 * 1024 * 1024) { "Request is too large" }
        connection.setRequestProperty("Content-Type", if (pngPath.isBlank()) "application/json" else "image/png")
        if (pngPath.isNotBlank()) {
            connection.setRequestProperty("X-Planner-Page", pageId)
            connection.setRequestProperty("X-Planner-Revision", revision.toInt().toString())
            require(body.length <= 7000) { "Too many preview regions" }
            connection.setRequestProperty("X-Planner-Regions", body)
        }
        try {
            connection.outputStream.use { it.write(bytes) }
            require(connection.responseCode in 200..299) { "Planner server returned HTTP ${connection.responseCode}; pending changes retained" }
            val data = connection.inputStream.use { readBounded(it, 8 * 1024 * 1024) }
            String(data, Charsets.UTF_8)
        } finally { connection.disconnect() }
    }
}
