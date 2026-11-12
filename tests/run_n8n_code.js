// Runs one n8n Code step's JavaScript outside n8n, for the tests (test_n8n_workflows.py). Reads {code, steps, input}
// as JSON on stdin and prints what the step returns: steps maps a step name to its first item's json, input is the
// step's own item.
const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('end', async () => {
  const { code, steps, input } = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  const $ = (name) => {
    if (!(name in steps)) throw new Error(`the test gave no step named ${name}`);
    return { first: () => ({ json: steps[name] }) };
  };
  const $input = { first: () => ({ json: input }) };
  const run = new Function('$', '$input', `return (async () => {\n${code}\n})();`);
  process.stdout.write(JSON.stringify(await run($, $input)));
});
