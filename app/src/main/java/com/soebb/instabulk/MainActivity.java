package com.soebb.instabulk;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.ClipboardManager;
import android.content.ClipData;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.MotionEvent;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final int PICK_LINKS = 41;
    private static final int EXPORT_DATABASE = 42;
    private static final int RESTORE_DATABASE = 43;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable logRefresh = new Runnable() {
        @Override public void run() {
            refreshLogs();
            handler.postDelayed(this, 1200);
        }
    };
    private EditText botToken, apiId, apiHash, targetChat, ownerId, backupChat;
    private TextView linksLabel, statusLabel, logView, databaseView;
    private LogScrollView logScroll;
    private File linksFile, logFile, databaseFile;
    private SharedPreferences prefs;
    private Button startButton;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("settings", MODE_PRIVATE);
        linksFile = new File(getFilesDir(), "links.txt");
        logFile = new File(getFilesDir(), "downloader.log");
        databaseFile = new File(getFilesDir(), "progress.sqlite3");
        buildScreen();
        loadSettings();
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 7);
        }
    }

    @Override protected void onResume() {
        super.onResume();
        handler.removeCallbacks(logRefresh);
        handler.post(logRefresh);
        refreshDatabase();
    }

    @Override protected void onPause() {
        handler.removeCallbacks(logRefresh);
        super.onPause();
    }

    private void buildScreen() {
        ScrollView screenScroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(20), dp(20), dp(24));
        screenScroll.addView(root);
        setContentView(screenScroll);

        TextView title = new TextView(this);
        title.setText("Instagram Bulk Downloader");
        title.setTextSize(23);
        title.setPadding(0, 0, 0, dp(16));
        root.addView(title);

        botToken = field(root, "BOT_TOKEN (Gogram MTProto bot auth)", true);
        apiId = field(root, "API_ID", false);
        apiHash = field(root, "API_HASH", true);
        targetChat = field(root, "TARGET_CHAT_ID (ex: @my_chnl)", false);
        ownerId = field(root, "OWNER_ID [optional]", false);
        backupChat = field(root, "BACKUP_CHAT_ID [optional]", false);

        Button choose = button("Choose links.txt");
        root.addView(choose);
        choose.setOnClickListener(v -> {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            intent.setType("text/plain");
            intent.addCategory(Intent.CATEGORY_OPENABLE);
            startActivityForResult(intent, PICK_LINKS);
        });
        linksLabel = new TextView(this);
        linksLabel.setText(linksFile.exists() ? "A links.txt file is ready. Choose another file to replace it." : "No links file selected");
        linksLabel.setPadding(0, dp(8), 0, dp(12));
        root.addView(linksLabel);

        Button save = button("Save settings");
        root.addView(save);
        save.setOnClickListener(v -> saveSettings(true));

        startButton = button("Start");
        root.addView(startButton);
        startButton.setOnClickListener(v -> {
            if (!saveSettings(false)) return;
            if (!linksFile.exists() || linksFile.length() == 0) {
                toast("Select a non-empty links.txt file first");
                return;
            }
            Intent service = new Intent(this, DownloaderService.class).setAction(DownloaderService.ACTION_START);
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(service); else startService(service);
            statusLabel.setText("Starting…");
            startButton.setEnabled(false);
            startButton.setText("Starting…");
            handler.postDelayed(() -> {
                startButton.setEnabled(true);
                startButton.setText("Start");
            }, 2500);
        });

        Button pause = button("Pause");
        root.addView(pause);
        pause.setOnClickListener(v -> sendControl(DownloaderService.ACTION_PAUSE));
        Button resume = button("Resume");
        root.addView(resume);
        resume.setOnClickListener(v -> sendControl(DownloaderService.ACTION_RESUME));
        Button stop = button("Stop");
        root.addView(stop);
        stop.setOnClickListener(v -> sendControl(DownloaderService.ACTION_STOP));

        statusLabel = new TextView(this);
        statusLabel.setText("Idle");
        statusLabel.setTextSize(15);
        statusLabel.setPadding(0, dp(18), 0, dp(14));
        root.addView(statusLabel);

        TextView dbTitle = new TextView(this);
        dbTitle.setText("Database");
        dbTitle.setTextSize(18);
        dbTitle.setPadding(0, dp(12), 0, dp(8));
        root.addView(dbTitle);

        databaseView = new TextView(this);
        databaseView.setText("Database has not been created yet. Start a queue to create it.");
        databaseView.setTextSize(13);
        databaseView.setPadding(dp(10), dp(10), dp(10), dp(10));
        databaseView.setBackgroundColor(0xFFEFEFEF);
        root.addView(databaseView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout dbActions = new LinearLayout(this);
        dbActions.setOrientation(LinearLayout.HORIZONTAL);
        Button refreshDb = button("Refresh DB");
        Button exportDb = button("Export backup");
        dbActions.addView(refreshDb, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        dbActions.addView(exportDb, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        root.addView(dbActions);
        refreshDb.setOnClickListener(v -> refreshDatabase());
        exportDb.setOnClickListener(v -> beginDatabaseExport());

        Button restoreDb = button("Restore database backup");
        root.addView(restoreDb);
        restoreDb.setOnClickListener(v -> {
            if (DownloaderService.isRunning) {
                toast("Stop the downloader before restoring a database");
                return;
            }
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            intent.setType("*/*");
            intent.addCategory(Intent.CATEGORY_OPENABLE);
            startActivityForResult(intent, RESTORE_DATABASE);
        });
        TextView restoreHint = new TextView(this);
        restoreHint.setText("To restore a Telegram backup, download its .sqlite3 document to your phone, then select it here. Restore replaces the current database.");
        restoreHint.setTextSize(12);
        restoreHint.setPadding(0, dp(4), 0, dp(12));
        root.addView(restoreHint);

        TextView logTitle = new TextView(this);
        logTitle.setText("Activity log");
        logTitle.setTextSize(18);
        logTitle.setPadding(0, dp(12), 0, dp(8));
        root.addView(logTitle);

        logScroll = new LogScrollView();
        logScroll.setFillViewport(false);
        logScroll.setVerticalScrollBarEnabled(true);
        logScroll.setSmoothScrollingEnabled(true);
        logView = new TextView(this);
        logView.setText("Waiting for downloader logs…");
        logView.setTextSize(12);
        logView.setTextIsSelectable(false);
        logView.setPadding(dp(10), dp(10), dp(10), dp(10));
        logView.setBackgroundColor(0xFFEFEFEF);
        logScroll.addView(logView, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(logScroll, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(300)));
        LinearLayout logActions = new LinearLayout(this);
        logActions.setOrientation(LinearLayout.HORIZONTAL);
        Button refreshLog = button("Refresh log");
        Button copyLog = button("Copy log");
        logActions.addView(refreshLog, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        logActions.addView(copyLog, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        root.addView(logActions);
        refreshLog.setOnClickListener(v -> refreshLogs());
        copyLog.setOnClickListener(v -> copyLogs());
    }

    private class LogScrollView extends ScrollView {
        LogScrollView() { super(MainActivity.this); }
        @Override public boolean onInterceptTouchEvent(MotionEvent event) {
            if (event.getActionMasked() == MotionEvent.ACTION_DOWN && getParent() != null) {
                getParent().requestDisallowInterceptTouchEvent(true);
            }
            return super.onInterceptTouchEvent(event);
        }
        @Override public boolean onTouchEvent(MotionEvent event) {
            boolean handled = super.onTouchEvent(event);
            int action = event.getActionMasked();
            if ((action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) && getParent() != null) {
                getParent().requestDisallowInterceptTouchEvent(false);
            }
            return handled;
        }
    }

    private EditText field(LinearLayout root, String hint, boolean secret) {
        EditText edit = new EditText(this);
        edit.setSingleLine(true);
        edit.setHint(hint);
        edit.setInputType(secret ? 129 : 1);
        root.addView(edit, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(54)));
        return edit;
    }

    private Button button(String text) {
        Button b = new Button(this);
        b.setText(text);
        return b;
    }

    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + 0.5f); }

    private boolean saveSettings(boolean showMessage) {
        String token = botToken.getText().toString().trim();
        String id = apiId.getText().toString().trim();
        String hash = apiHash.getText().toString().trim();
        String chat = targetChat.getText().toString().trim();
        if (token.isEmpty() || id.isEmpty() || hash.isEmpty() || chat.isEmpty()) {
            toast("Gogram BOT_TOKEN, API_ID, API_HASH and TARGET_CHAT_ID are required");
            return false;
        }
        try { Integer.parseInt(id); } catch (NumberFormatException e) {
            toast("API_ID must be a number");
            return false;
        }
        prefs.edit()
            .putString("BOT_TOKEN", token).putString("API_ID", id)
            .putString("API_HASH", hash).putString("TARGET_CHAT_ID", chat)
            .putString("OWNER_ID", ownerId.getText().toString().trim())
            .putString("BACKUP_CHAT_ID", backupChat.getText().toString().trim())
            .apply();
        if (showMessage) toast("Settings saved on this device");
        return true;
    }

    private void loadSettings() {
        botToken.setText(prefs.getString("BOT_TOKEN", ""));
        apiId.setText(prefs.getString("API_ID", ""));
        apiHash.setText(prefs.getString("API_HASH", ""));
        targetChat.setText(prefs.getString("TARGET_CHAT_ID", ""));
        ownerId.setText(prefs.getString("OWNER_ID", ""));
        backupChat.setText(prefs.getString("BACKUP_CHAT_ID", ""));
    }

    private void sendControl(String action) {
        Intent intent = new Intent(this, DownloaderService.class).setAction(action);
        startService(intent);
    }

    private void refreshLogs() {
        if (logView == null) return;
        if (!logFile.exists()) {
            String empty = "No log file yet. Start the downloader to see activity here.";
            if (!empty.contentEquals(logView.getText())) logView.setText(empty);
            return;
        }
        try (RandomAccessFile file = new RandomAccessFile(logFile, "r")) {
            long length = file.length();
            long start = Math.max(0, length - 48000);
            file.seek(start);
            byte[] bytes = new byte[(int) (length - start)];
            file.readFully(bytes);
            String content = new String(bytes, StandardCharsets.UTF_8);
            if (!content.contentEquals(logView.getText())) {
                int previousY = logScroll.getScrollY();
                logView.setText(content.isEmpty() ? "Log file is empty." : content);
                logScroll.post(() -> logScroll.scrollTo(0, Math.min(previousY,
                    Math.max(0, logView.getHeight() - logScroll.getHeight()))));
            }
        } catch (Exception e) {
            logView.setText("Could not read log: " + e.getMessage());
        }
    }

    private void copyLogs() {
        if (!logFile.exists()) {
            toast("No log file to copy");
            return;
        }
        try (RandomAccessFile file = new RandomAccessFile(logFile, "r")) {
            long length = file.length();
            long start = Math.max(0, length - 48000);
            file.seek(start);
            byte[] bytes = new byte[(int) (length - start)];
            file.readFully(bytes);
            String content = new String(bytes, StandardCharsets.UTF_8);
            ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(ClipData.newPlainText("Downloader log", content));
            toast("Latest log copied to clipboard");
        } catch (Exception e) {
            toast("Could not copy log: " + e.getMessage());
        }
    }

    private void refreshDatabase() {
        if (databaseView == null) return;
        if (!databaseFile.exists()) {
            databaseView.setText("Database has not been created yet. Start a queue to create it.");
            return;
        }
        SQLiteDatabase db = null;
        try {
            db = SQLiteDatabase.openDatabase(databaseFile.getAbsolutePath(), null, SQLiteDatabase.OPEN_READONLY);
            StringBuilder out = new StringBuilder();
            out.append("Database size: ").append(databaseFile.length() / 1024).append(" KB\n");
            Cursor counts = db.rawQuery("SELECT status, COUNT(*) FROM posts GROUP BY status", null);
            int done = 0, failed = 0, pending = 0, processing = 0;
            while (counts.moveToNext()) {
                String status = counts.getString(0);
                int count = counts.getInt(1);
                if ("done".equals(status)) done = count;
                else if ("failed".equals(status)) failed = count;
                else if ("pending".equals(status)) pending = count;
                else if ("processing".equals(status)) processing = count;
            }
            counts.close();
            out.append("Completed: ").append(done).append("   Failed: ").append(failed)
                .append("\nPending: ").append(pending).append("   Processing: ").append(processing)
                .append("\n\nRecent links:\n");
            Cursor recent = db.rawQuery("SELECT url,status,attempts,updated_at,last_error FROM posts ORDER BY updated_at DESC LIMIT 15", null);
            while (recent.moveToNext()) {
                out.append("• ").append(recent.getString(1).toUpperCase(Locale.ROOT))
                    .append(" | attempts: ").append(recent.getInt(2))
                    .append("\n").append(recent.getString(0)).append("\n");
                String error = recent.getString(4);
                if (error != null && !error.isEmpty()) out.append("Error: ").append(error).append("\n");
                out.append("\n");
            }
            recent.close();
            databaseView.setText(out.toString());
        } catch (Exception e) {
            databaseView.setText("Could not read database: " + e.getMessage());
        } finally {
            if (db != null) db.close();
        }
    }

    private void beginDatabaseExport() {
        if (DownloaderService.isRunning) {
            toast("Wait until the queue finishes before exporting the database");
            return;
        }
        if (!databaseFile.exists()) {
            toast("There is no database to export yet");
            return;
        }
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date());
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.setType("application/octet-stream");
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.putExtra(Intent.EXTRA_TITLE, "progress-backup-" + stamp + ".sqlite3");
        startActivityForResult(intent, EXPORT_DATABASE);
    }

    private void exportDatabase(Uri destination) {
        if (DownloaderService.isRunning) {
            toast("Stop the downloader before exporting the database");
            return;
        }
        if (!databaseFile.exists()) {
            toast("There is no database to export yet");
            return;
        }
        SQLiteDatabase db = null;
        try {
            db = SQLiteDatabase.openDatabase(databaseFile.getAbsolutePath(), null, SQLiteDatabase.OPEN_READWRITE);
            Cursor checkpoint = db.rawQuery("PRAGMA wal_checkpoint(FULL)", null);
            if (checkpoint.moveToFirst() && checkpoint.getInt(0) != 0) {
                checkpoint.close();
                throw new IllegalStateException("Database is busy; stop the downloader and try again");
            }
            checkpoint.close();
            db.close();
            db = null;
            try (InputStream in = new FileInputStream(databaseFile);
                 OutputStream out = getContentResolver().openOutputStream(destination, "w")) {
                if (out == null) throw new IllegalStateException("Cannot create destination file");
                byte[] buffer = new byte[32 * 1024];
                int n;
                while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
            }
            toast("Database backup exported");
        } catch (Exception e) {
            toast("Export failed: " + e.getMessage());
        } finally {
            if (db != null) db.close();
        }
    }

    private void restoreDatabase(Uri source) {
        if (DownloaderService.isRunning) {
            toast("Stop the downloader before restoring a database");
            return;
        }
        File incoming = new File(getCacheDir(), "restore-incoming.sqlite3");
        File staged = new File(getFilesDir(), "progress-restore-staged.sqlite3");
        File previous = new File(getFilesDir(), "progress-before-restore.sqlite3");
        try {
            try (InputStream in = getContentResolver().openInputStream(source);
                 OutputStream out = new FileOutputStream(incoming)) {
                if (in == null) throw new IllegalStateException("Cannot open selected backup");
                byte[] buffer = new byte[32 * 1024];
                int n;
                while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
            }
            validateDatabase(incoming);
            copyFile(incoming, staged);
            validateDatabase(staged);
            checkpointExistingDatabase();
            if (databaseFile.exists()) copyFile(databaseFile, previous);
            new File(getFilesDir(), "progress.sqlite3-wal").delete();
            new File(getFilesDir(), "progress.sqlite3-shm").delete();
            try {
                copyFile(staged, databaseFile);
                validateDatabase(databaseFile);
            } catch (Exception replacementError) {
                if (previous.exists()) copyFile(previous, databaseFile);
                throw replacementError;
            }
            toast("Database restored successfully");
            refreshDatabase();
        } catch (Exception e) {
            toast("Restore failed: " + e.getMessage());
        } finally {
            incoming.delete();
            staged.delete();
        }
    }

    private void checkpointExistingDatabase() {
        if (!databaseFile.exists()) return;
        SQLiteDatabase db = SQLiteDatabase.openDatabase(databaseFile.getAbsolutePath(), null, SQLiteDatabase.OPEN_READWRITE);
        try {
            Cursor checkpoint = db.rawQuery("PRAGMA wal_checkpoint(FULL)", null);
            checkpoint.moveToFirst();
            boolean busy = checkpoint.getInt(0) != 0;
            checkpoint.close();
            if (busy) throw new IllegalStateException("Current database is busy; stop the downloader and retry");
        } finally {
            db.close();
        }
    }

    private void validateDatabase(File file) {
        SQLiteDatabase db = SQLiteDatabase.openDatabase(file.getAbsolutePath(), null, SQLiteDatabase.OPEN_READONLY);
        try {
            Cursor c = db.rawQuery("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('posts','chunks')", null);
            int found = c.getCount();
            c.close();
            if (found < 2) throw new IllegalArgumentException("This file is not a valid downloader database backup");
            Cursor integrity = db.rawQuery("PRAGMA integrity_check", null);
            boolean valid = integrity.moveToFirst() && "ok".equalsIgnoreCase(integrity.getString(0));
            integrity.close();
            if (!valid) throw new IllegalArgumentException("Database integrity check failed");
        } finally {
            db.close();
        }
    }

    private void copyFile(File source, File destination) throws Exception {
        try (InputStream in = new FileInputStream(source); OutputStream out = new FileOutputStream(destination)) {
            byte[] buffer = new byte[32 * 1024];
            int n;
            while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
            out.flush();
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();
        if (requestCode == PICK_LINKS) {
            try (InputStream in = getContentResolver().openInputStream(uri);
                 FileOutputStream out = new FileOutputStream(linksFile)) {
                if (in == null) throw new IllegalStateException("Cannot open selected file");
                byte[] buffer = new byte[8192];
                int n;
                while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
                String text;
                try (InputStream countIn = new FileInputStream(linksFile);
                     ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                    byte[] countBuffer = new byte[8192];
                    int read;
                    while ((read = countIn.read(countBuffer)) != -1) bytes.write(countBuffer, 0, read);
                    text = new String(bytes.toByteArray(), StandardCharsets.UTF_8);
                }
                java.util.HashSet<String> unique = new java.util.HashSet<>();
                for (String line : text.split("\\R")) {
                    String value = line.trim();
                    if (value.startsWith("http") && !value.startsWith("#")) unique.add(value);
                }
                linksLabel.setText("Loaded " + unique.size() + " unique link(s). Select another file to replace this list.");
                toast("Links file imported");
            } catch (Exception e) {
                toast("Could not import file: " + e.getMessage());
            }
        } else if (requestCode == EXPORT_DATABASE) {
            exportDatabase(uri);
        } else if (requestCode == RESTORE_DATABASE) {
            new AlertDialog.Builder(this)
                .setTitle("Restore database?")
                .setMessage("This will replace the current download history with the selected backup. The previous database is kept as progress-before-restore.sqlite3.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Restore", (dialog, which) -> restoreDatabase(uri))
                .show();
        }
    }

    private void toast(String message) { Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }
}
