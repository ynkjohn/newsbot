// Test script for WhatsApp JID parsing logic
const testCases = [
  { jid: "5514996083583:23@s.whatsapp.net", expectedPhone: "5514996083583" },
  { jid: "5514996083583@s.whatsapp.net", expectedPhone: "5514996083583" },
  { jid: "5514996083583@lid", expectedPhone: "5514996083583" },
  { jid: "229373315686421@lid", expectedPhone: "229373315686421" },
  { jid: "1234567890:99@s.whatsapp.net", expectedPhone: "1234567890" },
  { jid: "", expectedPhone: "" },
  { jid: "invalid", expectedPhone: "" },
];

console.log("🧪 Testing JID phone number extraction:");
console.log("=".repeat(60));

testCases.forEach(({ jid, expectedPhone }, i) => {
  const botPhoneMatch = jid.match(/^(\d+)/);
  const botPhone = botPhoneMatch ? botPhoneMatch[1] : "";
  
  const passed = botPhone === expectedPhone;
  const status = passed ? "✅ PASS" : "❌ FAIL";
  
  console.log(`Test ${i+1}: ${status}`);
  console.log(`  Input: "${jid}"`);
  console.log(`  Expected: "${expectedPhone}"`);
  console.log(`  Got: "${botPhone}"`);
  console.log();
});

console.log("=".repeat(60));
console.log("\n📋 JID Variants Logic:");
console.log("For bot JID: '5514996083583:23@s.whatsapp.net'");
console.log("Extracted phone: '5514996083583'");
console.log("JID variants to check against mentions:");
console.log("1. '5514996083583:23@s.whatsapp.net' (original)");
console.log("2. '5514996083583@s.whatsapp.net' (standard)");
console.log("3. '5514996083583@lid' (Live ID)");
console.log("\nMention check: mentions.some(mention => variants.some(variant => mention.includes(phone)))");
console.log("=".repeat(60));