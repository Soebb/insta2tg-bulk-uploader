package com.soebb.instabulk;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

public class DownloaderService extends Service {
    public static final String ACTION_START = "com.soebb.instabulk.START";
    public static final String ACTION_PAUSE = "com.soebb.instabulk.PAUSE";
    public static final String ACTION_RESUME = "com.soebb.instabulk.RESUME";
    public static final String ACTION_STOP = "com.soebb.instabulk.STOP";
    private static final String CHANNEL = "downloader";
    public static volatile boolean isRunning = false;
    private Thread worker;
    private volatile PyObject engine;
    private volatile boolean stopRequested = false;
    private volatile boolean pauseRequested = false;

    @Override public void onCreate() {
        super.onCreate();
        createChannel();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_PAUSE.equals(action) || ACTION_RESUME.equals(action) || ACTION_STOP.equals(action)) {
            if (ACTION_STOP.equals(action)) {
                stopRequested = true;
                pauseRequested = false;
                updateNotification("Stop requested…");
            } else if (ACTION_PAUSE.equals(action)) {
                pauseRequested = true;
                updateNotification("Pausing…");
            } else {
                pauseRequested = false;
                updateNotification("Resuming…");
            }
            Thread control = new Thread(() -> {
                try {
                    PyObject current = engine;
                    if (current != null) {
                        String method = ACTION_PAUSE.equals(action) ? "pause" :
                            ACTION_RESUME.equals(action) ? "resume" : "stop";
                        current.callAttr(method);
                    }
                } catch (Exception e) {
                    android.util.Log.e("InstagramBulk", "Could not send engine control", e);
                }
            }, "downloader-control");
            control.start();
            if ((worker == null || !worker.isAlive()) && ACTION_STOP.equals(action)) {
                stopRequested = false;
                stopSelf();
            }
            return START_STICKY;
        }

        startForeground(1, notification("Preparing downloader…"));
        if (worker == null || !worker.isAlive()) {
            isRunning = true;
            worker = new Thread(() -> {
                try {
                    if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                    engine = Python.getInstance().getModule("engine");
                    if (stopRequested) engine.callAttr("stop");
                    else if (pauseRequested) engine.callAttr("pause");
                    SharedPreferences p = getSharedPreferences("settings", MODE_PRIVATE);
                    JSONObject config = new JSONObject();
                    for (String key : new String[]{"BOT_TOKEN", "API_ID", "API_HASH", "TARGET_CHAT_ID", "OWNER_ID", "BACKUP_CHAT_ID"}) {
                        config.put(key, p.getString(key, ""));
                    }
                    config.put("REQUEST_DELAY", "2");
                    config.put("STOP_REQUESTED", stopRequested);
                    config.put("PAUSE_REQUESTED", pauseRequested);
                    String result = engine.callAttr("start", getFilesDir().getAbsolutePath(), config.toString()).toString();
                    updateNotification(result);
                } catch (Exception e) {
                    updateNotification("Error: " + e.getMessage());
                    android.util.Log.e("InstagramBulk", "Python engine failed", e);
                } finally {
                    engine = null;
                    stopRequested = false;
                    pauseRequested = false;
                    isRunning = false;
                    stopForeground(false);
                    stopSelf();
                }
            }, "downloader-worker");
            worker.start();
        } else {
            updateNotification("Downloader is already running.");
        }
        return START_STICKY;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(CHANNEL, "Downloader", NotificationManager.IMPORTANCE_LOW);
            getSystemService(NotificationManager.class).createNotificationChannel(channel);
        }
    }

    private Notification notification(String text) {
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return builder.setContentTitle("Instagram Bulk Downloader")
            .setContentText(text).setSmallIcon(android.R.drawable.stat_sys_download)
            .setOngoing(true).build();
    }

    private void updateNotification(String text) {
        getSystemService(NotificationManager.class).notify(1, notification(text));
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
