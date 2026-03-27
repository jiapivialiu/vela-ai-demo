curl https://api.gmi-serving.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GMI_API_KEY" \
  -d '{
    "model": "deepseek-ai/DeepSeek-V3.2",
    "messages": [
      {
        "role": "developer",
        "content": "You are a helpful assistant."
      },
      {
        "role": "user",
        "content": "Hello!"
      }
    ],
    "stream": true
  }'

curl -X POST "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey/requests" \
  -H "Authorization: Bearer $GMI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedream-5.0-lite",
    "payload": {
      "prompt": "A cinematic widescreen shot of a futuristic neon city",
      "size": "2560x1440",
      "output_format": "png",
      "max_images": 1,
      "watermark": false
    }
  }'
