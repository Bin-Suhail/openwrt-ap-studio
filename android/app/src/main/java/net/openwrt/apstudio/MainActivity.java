package net.openwrt.apstudio;

import android.app.*;
import android.os.*;
import android.content.*;
import android.graphics.Color;
import android.net.Uri;
import android.provider.Settings;
import android.view.*;
import android.webkit.*;
import android.widget.*;
import com.chaquo.python.Python;
import java.io.*;

public class MainActivity extends Activity {
    private WebView web;
    private String origin;
    private static final int EXPORT = 41;
    private int selectedTransport = 0;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(16,28,43));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        Spinner transport = new Spinner(this);
        selectedTransport = RouterTransport.isEthernet() ? 1 : 0;
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, new String[]{"Wi-Fi", "USB Ethernet"});
        transport.setAdapter(adapter);
        transport.setSelection(selectedTransport);
        transport.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            public void onNothingSelected(AdapterView<?> parent) {}
            public void onItemSelected(AdapterView<?> parent, View view, int index, long id) {
                if (index != selectedTransport && active()) {
                    transport.setSelection(selectedTransport);
                    Toast.makeText(MainActivity.this, "انتظر اكتمال البرمجة قبل تغيير طريقة الاتصال", Toast.LENGTH_LONG).show();
                    return;
                }
                selectedTransport = index;
                RouterTransport.select(index == 1);
                if (view instanceof TextView) ((TextView)view).setTextColor(Color.WHITE);
            }
        });
        bar.addView(transport, new LinearLayout.LayoutParams(0, -2, 1));
        Button wifi = new Button(this); wifi.setText("الاتصال");
        wifi.setOnClickListener(v -> startActivity(new Intent(Settings.ACTION_WIFI_SETTINGS)));
        bar.addView(wifi);
        Button export = new Button(this); export.setText("النسخ"); export.setOnClickListener(v -> exportBackups()); bar.addView(export);
        root.addView(bar);
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(16,28,43));
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setSaveFormData(false);
        web.getSettings().setCacheMode(WebSettings.LOAD_NO_CACHE);
        web.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return origin == null || !request.getUrl().toString().equals(origin + "/");
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (origin != null && url.startsWith(origin + "/")) return null;
                return new WebResourceResponse("text/plain", "UTF-8", new ByteArrayInputStream(new byte[0]));
            }
        });
        root.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        Intent service = new Intent(this, LocalService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(service); else startService(service);
        new Thread(() -> {
            try { LocalService.ready.await(); } catch (InterruptedException ignored) { return; }
            runOnUiThread(() -> {
                if (LocalService.failed || LocalService.origin == null) {
                    new AlertDialog.Builder(this).setMessage("تعذر بدء الأداة. أغلق التطبيق وافتحه من جديد.").setPositiveButton("حسنًا", null).show();
                } else { origin = LocalService.origin; web.loadUrl(origin + "/"); }
            });
        }).start();
    }
    private boolean active() {
        try { return Python.isStarted() && Python.getInstance().getModule("android_entry").callAttr("active").toBoolean(); }
        catch (Exception ignored) { return false; }
    }
    private void exportBackups() {
        if (!Python.isStarted()) return;
        if (active()) { Toast.makeText(this, "انتظر اكتمال البرمجة أولًا", Toast.LENGTH_SHORT).show(); return; }
        Intent create = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        create.setType("application/zip"); create.addCategory(Intent.CATEGORY_OPENABLE);
        create.putExtra(Intent.EXTRA_TITLE, "APStudio-backups.zip"); startActivityForResult(create, EXPORT);
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != EXPORT || result != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();
        new Thread(() -> {
            try {
                String path = Python.getInstance().getModule("android_entry").callAttr("export_backups", getFilesDir().getAbsolutePath()).toString();
                try (InputStream input = new FileInputStream(path); OutputStream output = getContentResolver().openOutputStream(uri)) {
                    if (output == null) throw new IOException();
                    byte[] buffer = new byte[8192]; int n;
                    while ((n = input.read(buffer)) != -1) output.write(buffer, 0, n);
                }
                runOnUiThread(() -> Toast.makeText(this, "حُفظت النسخ الاحتياطية", Toast.LENGTH_LONG).show());
            } catch (Exception ignored) { runOnUiThread(() -> Toast.makeText(this, "تعذر حفظ النسخ", Toast.LENGTH_LONG).show()); }
        }).start();
    }
    @Override public void onBackPressed() {
        if (active()) { Toast.makeText(this, "البرمجة جارية؛ انتظر اكتمالها", Toast.LENGTH_LONG).show(); return; }
        new AlertDialog.Builder(this).setMessage("إغلاق أداة البرمجة؟").setNegativeButton("بقاء", null)
            .setPositiveButton("إغلاق", (dialog, which) -> { stopService(new Intent(this, LocalService.class)); finishAndRemoveTask(); android.os.Process.killProcess(android.os.Process.myPid()); }).show();
    }
}
