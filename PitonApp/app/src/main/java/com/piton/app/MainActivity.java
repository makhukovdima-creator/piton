package com.piton.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.ValueCallback;
import android.widget.Button;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.HashMap;
import java.util.Map;

public class MainActivity extends AppCompatActivity {

    public static final String PREFS = "piton_prefs";
    public static final String KEY_URL = "server_url";
    public static final String KEY_TOKEN = "server_token";
    public static final String DEFAULT_URL = "https://liquefy-perfume-choice.ngrok-free.dev";
    // Токен НЕ хранится в приложении — вводится один раз в настройках и лежит в SharedPreferences.
    public static final String DEFAULT_TOKEN = "";
    public static final String BEACON_URL = "https://gist.githubusercontent.com/makhukovdima-creator/635d1b396f60e490a77d4db63f30cc1f/raw";
    public static final String CHANNEL_ID = "piton_initiator";

    public static volatile boolean settingsChanged = false;

    private WebView webView;
    private static final int FILE_CHOOSER_REQUEST = 1001;
    private ValueCallback<Uri[]> filePathCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.webview);
        Button btnSettings = findViewById(R.id.btn_settings);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);

        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView wv, ValueCallback<Uri[]> filePath, FileChooserParams params) {
                if (filePathCallback != null) filePathCallback.onReceiveValue(null);
                filePathCallback = filePath;
                Intent intent = params.createIntent();
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                try {
                    startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                } catch (ActivityNotFoundException e) {
                    filePathCallback = null;
                    return false;
                }
                return true;
            }
        });

        btnSettings.setOnClickListener(v ->
                startActivity(new Intent(this, SettingsActivity.class)));

        ensureNotificationChannel();
        requestNotifPermission();
        startPollService();
        syncKnownCount();
        loadServer();
    }

    private void startPollService() {
        Intent i = new Intent(this, PollService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(i);
        } else {
            startService(i);
        }
    }

    /** Запоминает текущее число сообщений, чтобы сервис не слал уведомления за старое. */
    private void syncKnownCount() {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        final String url = sp.getString(KEY_URL, DEFAULT_URL);
        final String token = sp.getString(KEY_TOKEN, DEFAULT_TOKEN);
        new Thread(() -> {
            HttpURLConnection c = null;
            try {
                URL u = new URL(url + "/api/history");
                c = (HttpURLConnection) u.openConnection();
                c.setConnectTimeout(10000);
                c.setReadTimeout(10000);
                c.setRequestMethod("GET");
                c.setRequestProperty("X-Auth-Token", token);
                if (c.getResponseCode() != 200) return;
                BufferedReader r = new BufferedReader(
                        new InputStreamReader(c.getInputStream(), "UTF-8"));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
                JSONObject obj = new JSONObject(sb.toString());
                JSONArray hist = obj.optJSONArray("history");
                if (hist != null) {
                    int total = obj.optInt("total", hist.length());
                    sp.edit().putInt("known_count", total).apply();
                }
            } catch (Exception e) {
                // тихо
            } finally {
                if (c != null) c.disconnect();
            }
        }).start();
    }

    private void ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID, "Питон", NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("Когда Питон напишет сам");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    private void requestNotifPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                        != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this,
                    new String[]{Manifest.permission.POST_NOTIFICATIONS}, 100);
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
        nm.notify((int) (System.currentTimeMillis() % 10000), b.build());
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (settingsChanged) {
            settingsChanged = false;
            loadServer();
        }
    }

    private void loadServer() {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        final String url = sp.getString(KEY_URL, DEFAULT_URL);
        final String token = sp.getString(KEY_TOKEN, DEFAULT_TOKEN);
        // Первый запуск: токена ещё нет — открываем настройки, чтобы пользователь ввёл его.
        if (token == null || token.isEmpty()) {
            startActivity(new Intent(this, SettingsActivity.class));
            return;
        }
        loadUrlInternal(url, token);
        // Маяк: в фоне проверяем актуальный адрес (туннель мог смениться)
        new Thread(() -> {
            String fresh = fetchBeacon();
            if (fresh != null && fresh.startsWith("http") && !fresh.equals(url)) {
                sp.edit().putString(KEY_URL, fresh).apply();
                runOnUiThread(() -> loadUrlInternal(fresh, token));
            }
        }).start();
    }

    private void loadUrlInternal(String url, String token) {
        if (token != null && !token.isEmpty()) {
            url += (url.contains("?") ? "&" : "?") + "token=" + token;
        }
        Map<String, String> headers = new HashMap<>();
        headers.put("ngrok-skip-browser-warning", "true");
        webView.loadUrl(url, headers);
    }

    /** Читает адрес сервера из маяка (GitHub gist). Может вызываться из любого потока. */
    public static String fetchBeacon() {
        HttpURLConnection c = null;
        try {
            URL u = new URL(BEACON_URL);
            c = (HttpURLConnection) u.openConnection();
            c.setConnectTimeout(10000);
            c.setReadTimeout(10000);
            c.setRequestMethod("GET");
            c.setRequestProperty("User-Agent", "piton-app");
            if (c.getResponseCode() != 200) return null;
            BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), "UTF-8"));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) sb.append(line);
            r.close();
            String s = sb.toString().trim();
            return s.isEmpty() ? null : s;
        } catch (Exception e) {
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER_REQUEST) {
            if (filePathCallback == null) return;
            Uri[] results = null;
            if (resultCode == RESULT_OK && data != null) {
                String dataString = data.getDataString();
                ClipData clipData = data.getClipData();
                if (clipData != null) {
                    results = new Uri[clipData.getItemCount()];
                    for (int i = 0; i < clipData.getItemCount(); i++) {
                        results[i] = clipData.getItemAt(i).getUri();
                    }
                } else if (dataString != null) {
                    results = new Uri[]{Uri.parse(dataString)};
                }
            }
            filePathCallback.onReceiveValue(results);
            filePathCallback = null;
        } else {
            super.onActivityResult(requestCode, resultCode, data);
        }
    }
}
