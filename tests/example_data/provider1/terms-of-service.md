# {{ provider_name }} Terms of Service

## 1. Introduction

Welcome to {{ provider_name }}'s services. By using our AI models and APIs, you agree to these terms of service.

## 2. Acceptable Use

You must use our services responsibly and in accordance with our usage policies:

- No harmful or malicious content generation
- Respect intellectual property rights
- Comply with applicable laws and regulations

## 3. API Usage

Our APIs are provided under the following conditions:

- Rate limits apply based on your subscription tier
- Usage monitoring for billing and compliance
- Service availability subject to maintenance windows

## 4. Data Privacy

We take your privacy seriously:

- Data sent to our APIs is processed according to our privacy policy
- We do not train on data sent via our API by default
- You retain ownership of your input and output data

## 5. Liability and Disclaimers

- Services provided "as is" without warranties
- Limited liability for damages
- Users responsible for their use of generated content

## 6. Contact

For questions about these terms, contact us at {{ contact_email }}

{% if enterprise_support %}
Enterprise customers can reach our dedicated support team at {{ enterprise_contact_email }}
{% endif %}

Last updated: {{ last_updated | strftime('%B %Y') }}
