Use the ComfyClaude MCP tools to generate an image based on $ARGUMENTS.

Steps:
1. Call `list_templates` to see available workflow templates
2. Pick the most appropriate template based on its model and description
3. Call `get_template` with the chosen name to see its required inputs
4. Call `queue_prompt` with the template name, inputs dict, and optional aspect_ratio
5. Call `check_job` with `wait: 30` to poll for completion
6. If status is `running`, call `check_job` again with `wait: 30`
7. Once status is `completed`, call `get_image` to get the output file path
8. Show the user the absolute file path to the generated image
