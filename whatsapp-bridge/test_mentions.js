// Test script for mention detection logic
const testMentionCases = [
  {
    description: "Bot mentioned with @lid format",
    botJid: "5514996083583:23@s.whatsapp.net",
    mentions: ["5514996083583@lid"],
    expected: true,
  },
  {
    description: "Bot mentioned with @s.whatsapp.net format",
    botJid: "5514996083583:23@s.whatsapp.net",
    mentions: ["5514996083583@s.whatsapp.net"],
    expected: true,
  },
  {
    description: "Bot mentioned with original JID",
    botJid: "5514996083583:23@s.whatsapp.net",
    mentions: ["5514996083583:23@s.whatsapp.net"],
    expected: true,
  },
  {
    description: "Different bot mentioned",
    botJid: "5514996083583:23@s.whatsapp.net",
    mentions: ["229373315686421@lid"],
    expected: false,
  },
  {
    description: "Multiple mentions including bot",
    botJid: "5514996083583:23@s.whatsapp.net",
    mentions: ["229373315686421@lid", "5514996083583@lid"],
    expected: true,
  },
  {
    description: "Empty mentions array",
    botJid: "5514996083583:23@s.whatsapp.net",
    mentions: [],
    expected: false,
  },
  {
    description: "Bot JID empty",
    botJid: "",
    mentions: ["5514996083583@lid"],
    expected: false,
  },
];

console.log("🧪 Testing mention detection logic:");
console.log("=".repeat(60));

testMentionCases.forEach(({ description, botJid, mentions, expected }, i) => {
  // Extract phone number from bot JID
  const botPhoneMatch = botJid.match(/^(\d+)/);
  const botPhone = botPhoneMatch ? botPhoneMatch[1] : "";
  
  // Check if bot is mentioned
  let isBotMentioned = false;
  let botJidVariants = [];
  if (botPhone) {
    botJidVariants = [
      botJid, // Original JID
      `${botPhone}@s.whatsapp.net`, // Standard format
      `${botPhone}@lid`, // Live ID format
    ];
    
    isBotMentioned = mentions.some(mention => 
      botJidVariants.some(variant => mention.includes(botPhone))
    );
  }
  
  const passed = isBotMentioned === expected;
  const status = passed ? "✅ PASS" : "❌ FAIL";
  
  console.log(`Test ${i+1}: ${status} - ${description}`);
  console.log(`  Bot JID: "${botJid}"`);
  console.log(`  Bot Phone: "${botPhone}"`);
  console.log(`  Mentions: ${JSON.stringify(mentions)}`);
  console.log(`  Variants: ${JSON.stringify(botJidVariants)}`);
  console.log(`  Expected: ${expected}, Got: ${isBotMentioned}`);
  console.log();
});

console.log("=".repeat(60));
console.log("\n📋 Real-world scenario from logs:");
console.log("Bot JID: '5514996083583:23@s.whatsapp.net'");
console.log("Extracted phone: '5514996083583'");
console.log("Mention received: '229373315686421@lid'");
console.log("Check: '229373315686421@lid'.includes('5514996083583') = false ❌");
console.log("Result: Bot NOT mentioned (correct!)");
console.log("\n✅ The bot should only respond when mentioned with its own number.");
console.log("=".repeat(60));