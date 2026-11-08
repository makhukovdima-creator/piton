package com.piton.app;

import android.content.SharedPreferences;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

public class SettingsActivity extends AppCompatActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_settings);

        EditText etUrl = findViewById(R.id.et_url);
        EditText etToken = findViewById(R.id.et_token);
        Button btnFind = findViewById(R.id.btn_find);
        Button btnSave = findViewById(R.id.btn_save);

        SharedPreferences sp = getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE);
        etUrl.setText(sp.getString(MainActivity.KEY_URL, MainActivity.DEFAULT_URL));
        etToken.setText(sp.getString(MainActivity.KEY_TOKEN, MainActivity.DEFAULT_TOKEN));

        btnFind.setOnClickListener(v -> {
            Toast.makeText(this, "Ищу сервер по маяку…", Toast.LENGTH_SHORT).show();
            new Thread(() -> {
                String fresh = MainActivity.fetchBeacon();
                runOnUiThread(() -> {
                    if (fresh == null || fresh.isEmpty() || !fresh.startsWith("http")) {
                        Toast.makeText(this, "Маяк не отвечает — проверь интернет", Toast.LENGTH_LONG).show();
                        return;
                    }
                    etUrl.setText(fresh);
                    sp.edit().putString(MainActivity.KEY_URL, fresh).apply();
                    MainActivity.settingsChanged = true;
                    Toast.makeText(this, "Найдено: " + fresh, Toast.LENGTH_LONG).show();
                    finish();
                });
            }).start();
        });

        btnSave.setOnClickListener(v -> {
            String url = etUrl.getText().toString().trim();
            String token = etToken.getText().toString().trim();
            if (url.isEmpty()) {
                Toast.makeText(this, "Введи адрес сервера", Toast.LENGTH_SHORT).show();
                return;
            }
            if (!url.startsWith("http")) {
                url = "https://" + url;
            }
            sp.edit()
                    .putString(MainActivity.KEY_URL, url)
                    .putString(MainActivity.KEY_TOKEN, token)
                    .apply();
            MainActivity.settingsChanged = true;
            finish();
        });
    }
}
