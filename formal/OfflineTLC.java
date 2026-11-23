import java.io.IOException;
import java.net.ServerSocket;
import java.net.Socket;
import java.rmi.server.RMISocketFactory;

/** Run standalone TLC without exporting its unused fingerprint store over RMI. */
class OfflineTLC {
    private static final class OfflineServerSocket extends ServerSocket {
        private boolean closed;

        OfflineServerSocket() throws IOException {
            super();
        }

        @Override
        public synchronized Socket accept() throws IOException {
            while (!closed) {
                try {
                    wait();
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                    throw new IOException("Offline RMI listener interrupted", error);
                }
            }
            throw new IOException("Offline RMI listener closed");
        }

        @Override
        public synchronized void close() {
            closed = true;
            notifyAll();
        }
    }

    public static void main(String[] args) throws Exception {
        int mainIndex = java.util.Arrays.asList(args).indexOf("tlc2.TLC");
        if (mainIndex >= 0) {
            for (int index = 0; index < mainIndex; index++) {
                if (args[index].startsWith("-D")) {
                    String[] property = args[index].substring(2).split("=", 2);
                    System.setProperty(property[0], property.length == 2 ? property[1] : "");
                }
            }
            args = java.util.Arrays.copyOfRange(args, mainIndex + 1, args.length);
        }
        RMISocketFactory.setSocketFactory(new RMISocketFactory() {
            @Override
            public Socket createSocket(String host, int port) throws IOException {
                throw new IOException("Standalone TLC cannot use remote RMI");
            }

            @Override
            public ServerSocket createServerSocket(int port) throws IOException {
                return new OfflineServerSocket();
            }
        });
        tlc2.TLC.main(args);
    }
}
