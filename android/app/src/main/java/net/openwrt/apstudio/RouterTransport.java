package net.openwrt.apstudio;

import android.content.Context;
import android.net.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;

/** Only the network type explicitly selected in this app can reach the entered router. */
public final class RouterTransport {
    private static Context context;
    private static volatile int transport = NetworkCapabilities.TRANSPORT_WIFI;
    public static void init(Context value) { context = value.getApplicationContext(); }
    public static void select(boolean ethernet) {
        transport = ethernet ? NetworkCapabilities.TRANSPORT_ETHERNET : NetworkCapabilities.TRANSPORT_WIFI;
    }
    public static boolean isEthernet() { return transport == NetworkCapabilities.TRANSPORT_ETHERNET; }
    public static String post(String address, String body, int timeout) throws Exception {
        URL url = new URL(address);
        if (!"http".equals(url.getProtocol()) || !"/ubus".equals(url.getPath()) || url.getUserInfo() != null)
            throw new IOException("Router endpoint rejected");
        InetAddress ip = InetAddress.getByName(url.getHost());
        ConnectivityManager cm = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        Network selected = null;
        int best = -1;
        for (Network network : cm.getAllNetworks()) {
            NetworkCapabilities caps = cm.getNetworkCapabilities(network);
            LinkProperties links = cm.getLinkProperties(network);
            if (caps == null || links == null || !caps.hasTransport(transport) || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue;
            for (RouteInfo route : links.getRoutes()) {
                // A specific local route is required: never use a cellular or Internet default route.
                int prefix = route.getDestination().getPrefixLength();
                if (prefix > best && prefix > 0 && route.matches(ip)) { selected = network; best = prefix; }
            }
        }
        if (selected == null) throw new IOException("No local route on chosen Wi-Fi/Ethernet network");
        HttpURLConnection connection = (HttpURLConnection) selected.openConnection(url, java.net.Proxy.NO_PROXY);
        connection.setConnectTimeout(timeout);
        connection.setReadTimeout(timeout);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestMethod("POST");
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json");
        try {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream output = connection.getOutputStream()) { output.write(bytes); }
            if (connection.getResponseCode() != 200) throw new IOException("Router HTTP error");
            try (InputStream input = connection.getInputStream(); ByteArrayOutputStream result = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[8192];
                int n;
                while ((n = input.read(buffer)) != -1) {
                    if (result.size() + n > 4 * 1024 * 1024) throw new IOException("Router response too large");
                    result.write(buffer, 0, n);
                }
                return result.toString("UTF-8");
            }
        } finally { connection.disconnect(); }
    }
}
