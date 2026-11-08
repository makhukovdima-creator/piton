package com.piton.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/** Фоновый сервис: опрашивает сервер каждые 20с и шлёт уведомления от инициатора. */
public class PollService extends Service {

    public static final String PREFS = "piton_prefs";
    public static final String KEY_URL = "server_url";
    public static final String KEY_TOKEN = "server_token";
    public static final String KEY_COUNT = "known_count";
    public static final String CHANNEL_ID = "piton_initiator";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable poll = new Runnable() {
        @Override public void run() {
            pollHistory();
            handler.postDelayed(this, 20000);
        }
    };
    private final Runnable beaconCheck = new Runnable() {
        @Override public void run() {
            refreshUrlFromBeacon();
            handler.postDelayed(this, 120000);
        }
    };

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannel();
        Notification n = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                        .setContentTitle("Питон")
                        .setContentText("слушает…")
                        .setSmallIcon(R.mipmap.ic_launcher)
                        .build()
                : new Notification.Builder(this)
                        .setContentTitle("Питон")
                        .setContentText("слушает…")
                        .setSmallIcon(R.mipmap.ic_launcher)
                        .build();
        startForeground(1, n);
        handler.postDelayed(poll, 5000);
        handler.postDelayed(beaconCheck, 30000);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;  // система перезапустит сервис, если убьёт
    }

    @Override
    public void onDestroy() {
        handler.removeCallbacks(poll);
        handler.removeCallbacks(beaconCheck);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    private void ensureChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID, "Питон", NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("Когда Питон напишет сам");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    private void showNotif(String title, String body) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm == null) return;
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        b.setSmallIcon(R.mipmap.ic_launcher)
         .setContentTitle(title)
         .setContentText(body)
         .setAutoCancel(true);
        nm.notify((int) (System.currentTimeMillis() % 10000) + 1, b.build());
    }

    /** Проверяет маяк и обновляет адрес сервера, если туннель сменился. */
    private void refreshUrlFromBeacon() {
        new Thread(() -> {
            String fresh = MainActivity.fetchBeacon();
            if (fresh == null || !fresh.startsWith("http")) return;
            SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
            String old = sp.getString(KEY_URL, MainActivity.DEFAULT_URL);
            if (!fresh.equals(old)) {
                sp.edit().putString(KEY_URL, fresh).apply();
            }
        }).start();
    }

    private void pollHistory() {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        final String token = sp.getString(KEY_TOKEN, MainActivity.DEFAULT_TOKEN);
        // токен не введён — сервер всё равно вернёт 403, не тратим запросы
        if (token == null || token.isEmpty()) return;
        String url = sp.getString(KEY_URL, MainActivity.DEFAULT_URL);
        final String usedUrl = url;
        new Thread(() -> {
            HttpURLConnection c = null;
            try {
                URL u = new URL(usedUrl + "/api/history");
                c = (HttpURLConnection) u.openConnection();
                c.setConnectTimeout(10000);
                c.setReadTimeout(10000);
                c.setRequestMethod("GET");
                c.setRequestProperty("X-Auth-Token", token);
                if (c.getResponseCode() != 200) { refreshUrlFromBeacon(); return; }
                BufferedReader r = new BufferedReader(
                        new InputStreamReader(c.getInputStream(), "UTF-8"));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
                JSONObject obj = new JSONObject(sb.toString());
                JSONArray hist = obj.optJSONArray("history");
                if (hist == null) return;
                int total = obj.optInt("total", hist.length());
                final int known = sp.getInt(KEY_COUNT, -1);
                if (known < 0) {
                    sp.edit().putInt(KEY_COUNT, total).apply();
                    return;
                }
                if (total > known) {
                    // новые записи — последние (total-known), все они в слайсе из 30
                    int start = Math.max(0, hist.length() - (total - known));
                    for (int i = start; i < hist.length(); i++) {
                        JSONObject m = hist.optJSONObject(i);
                        if (m == null) continue;
                        String ai = m.optString("ai", "");
                        boolean fromInitiator = !m.has("user") || m.optString("user", "").isEmpty();
                        if (fromInitiator && !ai.isEmpty()) {
                            final String msg = ai;
                            showNotif("Питон", msg.length() > 120
                                    ? msg.substring(0, 120) + "…" : msg);
                        }
                    }
                    sp.edit().putInt(KEY_COUNT, total).apply();
                }
            } catch (Exception e) {
                // сеть недоступна — возможно, адрес сменился
                refreshUrlFromBeacon();
            } finally {
                if (c != null) c.disconnect();
            }
        }).start();
    }
}
