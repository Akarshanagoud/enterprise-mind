import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

/** Minimal JDK-only client (Java 11+).  javac EmindClient.java && java EmindClient "refund window" key */
public class EmindClient {
    public static void main(String[] args) throws Exception {
        String question = args.length > 0 ? args[0] : "How many days to request a refund?";
        String key = args.length > 1 ? args[1] : "dev-viewer-key";
        String body = "{\"question\":\"" + question.replace("\\", "\\\\").replace("\"", "\\\"") + "\"}";
        HttpRequest req = HttpRequest.newBuilder(URI.create("http://127.0.0.1:8000/ask"))
                .header("content-type", "application/json").header("x-api-key", key)
                .POST(HttpRequest.BodyPublishers.ofString(body)).build();
        HttpResponse<String> res = HttpClient.newHttpClient().send(req, HttpResponse.BodyHandlers.ofString());
        System.out.println(res.statusCode() + " " + res.body());
    }
}
