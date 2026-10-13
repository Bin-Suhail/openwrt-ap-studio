package net.openwrt.apstudio;

import android.app.*;
import android.content.Intent;
import android.os.*;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.util.concurrent.CountDownLatch;

public class LocalService extends Service {
    public static final CountDownLatch ready = new CountDownLatch(1);
    public static volatile String origin;
    public static volatile boolean failed;
    @Override public void onCreate() {
        super.onCreate();
        RouterTransport.init(this);
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) manager.createNotificationChannel(new NotificationChannel("local", "برمجة المودم", NotificationManager.IMPORTANCE_LOW));
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, "local") : new Notification.Builder(this);
        startForeground(10, builder.setSmallIcon(net.openwrt.apstudio.R.drawable.ic_power)
            .setContentTitle("OpenWrt AP Studio").setContentText("الأداة تعمل محليًا على الجوال")
            .setContentIntent(pending).setOngoing(true).build());
        new Thread(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                origin = Python.getInstance().getModule("android_entry").callAttr("start", getFilesDir().getAbsolutePath()).toString();
            } catch (Exception ignored) { failed = true; }
            finally { ready.countDown(); }
        }, "APStudio-local").start();
    }
    @Override public int onStartCommand(Intent intent, int flags, int id) { return START_NOT_STICKY; }
    @Override public IBinder onBind(Intent intent) { return null; }
}
