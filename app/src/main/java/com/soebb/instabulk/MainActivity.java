package com.soebb.instabulk;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.ClipboardManager;
import android.content.ClipData;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.RandomAccessFile;

public class MainActivity extends Activity {
    private static final int PICK_LINKS = 41;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable logRefresh = new Runnable() {
        @Override public void run() {
            refreshLogs();
            handler.postDelayed(this, 1200);
        }
    };
    private EditText botToken, apiId, apiHash, targetChat, ownerId, backupChat;
    private TextView linksLabel, statusLabel, logView;
    private ScrollView logScroll;
    private File linksFile, logFile;
    private SharedPreferences prefs;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("settings", MODE_PRIVATE);
        linksFile = new File(getFilesDir(), "links.txt");
        logFile = new File(getFilesDir(), "downloader.log");
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
    }

    @Override protected void onPause() {
        handler.removeCallbacks(logRefresh);
        super.onPause();
    }

    private void buildScreen() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(20), dp(20), dp(24));
        scroll.addView(root);
        setContentView(scroll);

        TextView title = new TextView(this);
        title.setText("Instagram Bulk Downloader");
        title.setTextSize(23);
        title.setPadding(0, 0, 0, dp(16));
        root.addView(title);

        botToken = field(root, "BOT_TOKEN", true);
        apiId = field(root, "API_ID", false);
        apiHash = field(root, "API_HASH", true);
        targetChat = field(root, "TARGET_CHAT_ID", false);
        ownerId = field(root, "OWNER_ID (optional)", false);
        backupChat = field(root, "BACKUP_CHAT_ID (optional)", false);

        Button choose = button("Choose links.txt");
        root.addView(choose);
        choose.setOnClickListener(v -> {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            intent.setType("text/plain");
            intent.addCategory(Intent.CATEGORY_OPENABLE);
            startActivityForResult(intent, PICK_LINKS);
        });
        linksLabel = new TextView(this);
        linksLabel.setText(linksFile.exists() ? "Links file ready" : "No links file selected");
        linksLabel.setPadding(0, dp(8), 0, dp(12));
        root.addView(linksLabel);

        Button save = button("Save settings");
        root.addView(save);
        save.setOnClickListener(v -> saveSettings());

        Button start = button("Start");
        root.addView(start);
        start.setOnClickListener(v -> {
            if (!saveSettings()) return;
            if (!linksFile.exists()) {
                toast("Select a links.txt file first");
                return;
            }
            Intent service = new Intent(this, DownloaderService.class).setAction(DownloaderService.ACTION_START);
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(service); else startService(service);
            statusLabel.setText("Starting…");
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

        TextView logTitle = new TextView(this);
        logTitle.setText("Activity log");
        logTitle.setTextSize(18);
        logTitle.setPadding(0, dp(12), 0, dp(8));
        root.addView(logTitle);

        logScroll = new ScrollView(this);
        logScroll.setFillViewport(true);
        logView = new TextView(this);
        logView.setText("Waiting for downloader logs…");
        logView.setTextSize(12);
        logView.setTextIsSelectable(true);
        logView.setPadding(dp(10), dp(10), dp(10), dp(10));
        logView.setBackgroundColor(0xFFEFEFEF);
        logScroll.addView(logView, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(logScroll, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(260)));
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

    private boolean saveSettings() {
        String token = botToken.getText().toString().trim();
        String id = apiId.getText().toString().trim();
        String hash = apiHash.getText().toString().trim();
        String chat = targetChat.getText().toString().trim();
        if (token.isEmpty() || id.isEmpty() || hash.isEmpty() || chat.isEmpty()) {
            toast("BOT_TOKEN, API_ID, API_HASH and TARGET_CHAT_ID are required");
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
        toast("Settings saved on this device");
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
            logView.setText("No log file yet. Start the downloader to see activity here.");
            return;
        }
        try {
            String content;
            try (RandomAccessFile file = new RandomAccessFile(logFile, "r")) {
                long length = file.length();
                long start = Math.max(0, length - 48000);
                file.seek(start);
                byte[] bytes = new byte[(int) (length - start)];
                file.readFully(bytes);
                content = new String(bytes, java.nio.charset.StandardCharsets.UTF_8);
            }
            // Preserve the reader's current position; never force-scroll to the newest line.
            int previousY = logScroll.getScrollY();
            logView.setText(content.isEmpty() ? "Log file is empty." : content);
            logScroll.post(() -> logScroll.scrollTo(0, previousY));
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
            String content = new String(bytes, java.nio.charset.StandardCharsets.UTF_8);
            ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(ClipData.newPlainText("Downloader log", content));
            toast("Latest log copied to clipboard");
        } catch (Exception e) {
            toast("Could not copy log: " + e.getMessage());
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_LINKS || resultCode != RESULT_OK || data == null) return;
        Uri uri = data.getData();
        try (InputStream in = getContentResolver().openInputStream(uri);
             FileOutputStream out = new FileOutputStream(linksFile)) {
            if (in == null) throw new IllegalStateException("Cannot open selected file");
            byte[] buffer = new byte[8192];
            int n;
            while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
            linksLabel.setText("Selected: " + linksFile.length() + " bytes");
            toast("Links file imported");
        } catch (Exception e) {
            toast("Could not import file: " + e.getMessage());
        }
    }

    private void toast(String message) { Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }
}
