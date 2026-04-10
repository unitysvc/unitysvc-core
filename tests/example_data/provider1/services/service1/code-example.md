# GPT-4 Code Examples

## Python Example

```python
from openai import OpenAI

client = OpenAI(api_key="your-api-key")

response = client.chat.completions.create(
    model="gpt-4",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain quantum computing in simple terms."}
    ],
    max_tokens=150,
    temperature=0.7
)

print(response.choices[0].message.content)
```

## Node.js Example

```javascript
import OpenAI from "openai";

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

async function main() {
  const completion = await openai.chat.completions.create({
    messages: [
      { role: "system", content: "You are a helpful assistant." },
      { role: "user", content: "Write a haiku about programming." },
    ],
    model: "gpt-4",
    max_tokens: 100,
    temperature: 0.8,
  });

  console.log(completion.choices[0].message.content);
}

main();
```

## cURL Example

```bash
curl https://api.openai.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "description": "gpt-4",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant."
      },
      {
        "role": "user",
        "content": "What are the benefits of renewable energy?"
      }
    ],
    "max_tokens": 200,
    "temperature": 0.6
  }'
```

## Function Calling Example

```python
import openai
import json

# Define a function
def get_weather(location):
    """Get the current weather for a location"""
    # This would normally call a weather API
    return f"The weather in {location} is sunny and 75°F"

response = client.chat.completions.create(
    model="gpt-4",
    messages=[
        {"role": "user", "content": "What's the weather like in San Francisco?"}
    ],
    functions=[
        {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city name"
                    }
                },
                "required": ["location"]
            }
        }
    ],
    function_call="auto"
)

# Handle function call if present
if response.choices[0].message.function_call:
    function_name = response.choices[0].message.function_call.name
    function_args = json.loads(response.choices[0].message.function_call.arguments)

    if function_name == "get_weather":
        weather_result = get_weather(function_args["location"])
        print(weather_result)
```

## Best Practices

1. **Rate Limiting**: Implement exponential backoff for rate limit handling
2. **Error Handling**: Always handle API errors gracefully
3. **Token Management**: Monitor token usage to stay within limits
4. **Security**: Never expose API keys in client-side code
5. **Caching**: Cache responses when appropriate to reduce API calls
