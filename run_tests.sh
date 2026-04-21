#!/usr/bin/env bash
# Run the complete test suite for NewsBot

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== NewsBot Test Suite ===${NC}\n"

# 1. Run syntax check
echo -e "${YELLOW}1. Checking Python syntax...${NC}"
python -m py_compile processor/llm_client.py processor/summarizer.py scheduler/jobs.py delivery/whatsapp_sender.py app.py
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Syntax check passed${NC}\n"
else
    echo -e "${YELLOW}✗ Syntax errors found${NC}\n"
    exit 1
fi

# 2. Run pytest with coverage
echo -e "${YELLOW}2. Running pytest with coverage...${NC}"
pytest tests/ -v --cov=processor --cov=delivery --cov=scheduler --cov-report=term-missing

# 3. Run specific test groups
echo -e "\n${YELLOW}3. Running LLM client tests...${NC}"
pytest tests/test_llm_client.py -v

echo -e "\n${YELLOW}4. Running summarizer tests...${NC}"
pytest tests/test_summarizer.py -v

echo -e "\n${YELLOW}5. Running WhatsApp sender tests...${NC}"
pytest tests/test_whatsapp_sender.py -v

echo -e "\n${YELLOW}6. Running webhook tests...${NC}"
pytest tests/test_webhook.py -v

echo -e "\n${YELLOW}7. Running categorizer tests...${NC}"
pytest tests/test_categorizer.py -v

echo -e "\n${GREEN}=== All tests completed ===${NC}"
