#!/bin/bash
# Connectivity test for service2
curl -sf "${UNITYSVC_BASE_URL}/health" -o /dev/null
