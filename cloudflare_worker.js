export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    
    // Check if it's an OPTIONS request for CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
      });
    }

    // Hardcoded Groq Keys for random rotation
    const keys = [
      "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",
      "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",
      "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",
      "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"
    ];
    
    // Pick a random key per request to load balance
    const randomKey = keys[Math.floor(Math.random() * keys.length)];

    // Change the hostname to Groq's API
    url.hostname = 'api.groq.com';

    // Create a new request based on the original one
    const newRequest = new Request(url.toString(), new Request(request));
    
    // Modify headers to look like a direct request to Groq
    newRequest.headers.set('Host', 'api.groq.com');
    
    // INJECT THE HARDCODED API KEY (overwrites whatever the Python script sent)
    newRequest.headers.set('Authorization', `Bearer ${randomKey}`);

    // Fetch the response from Groq
    const response = await fetch(newRequest);
    
    // Add CORS headers to the response
    const newResponse = new Response(response.body, response);
    newResponse.headers.set('Access-Control-Allow-Origin', '*');
    
    return newResponse;
  },
};
