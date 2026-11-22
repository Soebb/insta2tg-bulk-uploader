package com.soebb.instabulk;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public class DownloaderService extends Service {
    public static final String ACTION_START = "com.soebb.instabulk.START";
    public static final String ACTION_PAUSE = "com.soebb.instabulk.PAUSE";
    public static final String ACTION_RESUME = "com.soebb.instabulk.RESUME";
    public static final String ACTION_STOP = "com.soebb.instabulk.STOP";
    private static final String CHANNEL = "downloader";
    private Thread worker;

    @Override public void onCreate() {
        super.onCreate();
        createChannel();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_PAUSE.equals(action) || ACTION_RESUME.equals(action) || ACTION_STOP.equals(action)) {
            if (Python.isStarted()) {
                PyObject engine = Python.getInstance().getModule("engine");
                engine.callAttr(action.substring(action.lastIndexOf('.') + 1).toLowerCase());
            }
            if (ACTION_STOP.equals(action)) stopForeground(true);
            return START_STICKY;
        }
        startForeground(1, notification("Preparing downloader…"));
        if (worker == null || !worker.isAlive()) {
            worker = new Thread(() -> {
                try {
                    if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                    PyObject engine = Python.getInstance().getModule("engine");
                    String result = engine.callAttr("initialize", getFilesDir().getAbsolutePath()).toString();
                    updateNotification(result);
                } catch (Exception e) {
                    updateNotification("Error: " + e.getMessage());
                    android.util.Log.e("InstagramBulk", "Python engine failed", e);
                }
            }, "downloader-worker");
            worker.start();
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
