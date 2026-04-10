#!/bin/bash
# Connectivity test for service1
curl -sf "${UNITYSVC_BASE_URL}/health" -o /dev/null
