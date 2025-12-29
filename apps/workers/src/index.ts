interface Env {
  PLATFORM_KV: KVNamespace;
  RATE_LIMITS: KVNamespace;
  STORAGE: R2Bucket;
  VIDEO_QUEUE: Queue;
  VECTORIZE: VectorizeIndex;
  ENVIRONMENT: string;
  
  // Secrets (added via wrangler secret put)
  TURSO_PLATFORM_DB_URL?: string;
  TURSO_PLATFORM_AUTH_TOKEN?: string;
  CLERK_SECRET_KEY?: string;
  ANTHROPIC_API_KEY?: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    
    // CORS headers for development
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    };
    
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      // Health check - verifies all bindings
      if (url.pathname === "/health") {
        return Response.json({
          status: "ok",
          timestamp: new Date().toISOString(),
          environment: env.ENVIRONMENT,
          bindings: {
            kv_platform: !!env.PLATFORM_KV,
            kv_ratelimits: !!env.RATE_LIMITS,
            r2_storage: !!env.STORAGE,
            queue_video: !!env.VIDEO_QUEUE,
            vectorize: !!env.VECTORIZE,
          },
          secrets: {
            turso_url: !!env.TURSO_PLATFORM_DB_URL,
            turso_token: !!env.TURSO_PLATFORM_AUTH_TOKEN,
            clerk: !!env.CLERK_SECRET_KEY,
            anthropic: !!env.ANTHROPIC_API_KEY,
          }
        }, { headers: corsHeaders });
      }
      
      // Test KV read/write
      if (url.pathname === "/test/kv") {
        const testKey = "test-" + Date.now();
        await env.PLATFORM_KV.put(testKey, "Hello from KV!");
        const value = await env.PLATFORM_KV.get(testKey);
        await env.PLATFORM_KV.delete(testKey); // Cleanup
        return Response.json({ 
          success: true, 
          test: "kv",
          value 
        }, { headers: corsHeaders });
      }
      
      // Test R2 read/write
      if (url.pathname === "/test/r2") {
        const testKey = "test-file-" + Date.now() + ".txt";
        await env.STORAGE.put(testKey, "Hello from R2!");
        const object = await env.STORAGE.get(testKey);
        const text = await object?.text();
        await env.STORAGE.delete(testKey); // Cleanup
        return Response.json({ 
          success: true, 
          test: "r2",
          content: text 
        }, { headers: corsHeaders });
      }
      
      // Test Queue (send a message)
      if (url.pathname === "/test/queue") {
        await env.VIDEO_QUEUE.send({
          type: "test",
          timestamp: Date.now(),
          message: "Test queue message"
        });
        return Response.json({ 
          success: true, 
          test: "queue",
          message: "Message sent to queue" 
        }, { headers: corsHeaders });
      }
      
      // Test Vectorize (create and query a test vector)
      if (url.pathname === "/test/vectorize") {
        // Create a simple test vector (768 dimensions of 0.1)
        const testVector = new Array(768).fill(0.1);
        const testId = "test-" + Date.now();
        
        // Insert
        await env.VECTORIZE.upsert([{
          id: testId,
          values: testVector,
          metadata: { test: true, timestamp: Date.now() }
        }]);
        
        // Query
        const results = await env.VECTORIZE.query(testVector, {
          topK: 1,
          returnMetadata: true
        });
        
        // Cleanup
        await env.VECTORIZE.deleteByIds([testId]);
        
        return Response.json({ 
          success: true, 
          test: "vectorize",
          results 
        }, { headers: corsHeaders });
      }
      
      // Test Anthropic API (OAuth Bearer token)
      if (url.pathname === "/test/anthropic") {
        if (!env.ANTHROPIC_API_KEY) {
          return Response.json({ 
            success: false, 
            error: "ANTHROPIC_API_KEY not configured" 
          }, { status: 400, headers: corsHeaders });
        }
        
        const response = await fetch("https://api.anthropic.com/v1/messages", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${env.ANTHROPIC_API_KEY}`,
            "anthropic-version": "2023-06-01"
          },
          body: JSON.stringify({
            model: "claude-3-5-haiku-20241022",
            max_tokens: 100,
            messages: [{ role: "user", content: "Say 'API test successful!' and nothing else." }]
          })
        });
        
        const data = await response.json();
        return Response.json({ 
          success: response.ok, 
          test: "anthropic",
          authMethod: "oauth_bearer",
          response: data 
        }, { headers: corsHeaders });
      }
      
      // Default response
      return Response.json({
        name: "Platform API",
        version: "1.0.0",
        endpoints: [
          "GET /health - Check all bindings",
          "GET /test/kv - Test KV storage",
          "GET /test/r2 - Test R2 storage", 
          "GET /test/queue - Test Queue",
          "GET /test/vectorize - Test Vectorize",
          "GET /test/anthropic - Test Anthropic API"
        ]
      }, { headers: corsHeaders });
      
    } catch (error) {
      return Response.json({
        success: false,
        error: error instanceof Error ? error.message : "Unknown error"
      }, { status: 500, headers: corsHeaders });
    }
  },
  
  // Queue consumer (handles video processing jobs)
  async queue(batch: MessageBatch<any>, env: Env): Promise<void> {
    for (const message of batch.messages) {
      console.log("Processing queue message:", message.body);
      // TODO: Implement actual video processing
      message.ack();
    }
  }
};
