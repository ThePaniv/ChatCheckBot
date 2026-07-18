# IAM user for GitHub Actions CI. Its access key is generated here and mounted
# into the repo as Actions secrets (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY):
#
#   terraform output -raw github_ci_access_key_id     | gh secret set AWS_ACCESS_KEY_ID
#   terraform output -raw github_ci_secret_access_key | gh secret set AWS_SECRET_ACCESS_KEY
#
# The key material lives only in local state (git-ignored) and GitHub's secret
# store. Rotate by tainting the key: terraform apply -replace=aws_iam_access_key.github_ci

resource "aws_iam_user" "github_ci" {
  name = "github-cli"
}

resource "aws_iam_access_key" "github_ci" {
  user = aws_iam_user.github_ci.name
}

# Push/pull images to the bot's ECR repo (auth token is account-wide).
data "aws_iam_policy_document" "ecr_push" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrPushPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.bot.arn]
  }
}

resource "aws_iam_user_policy" "ecr_push" {
  name   = "ecr-push"
  user   = aws_iam_user.github_ci.name
  policy = data.aws_iam_policy_document.ecr_push.json
}

# Point both Lambdas at the freshly pushed image (and wait for the rollout).
data "aws_iam_policy_document" "lambda_deploy" {
  statement {
    sid    = "UpdateLambdaCode"
    effect = "Allow"
    actions = [
      "lambda:UpdateFunctionCode",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
    ]
    resources = [
      aws_lambda_function.webhook.arn,
      aws_lambda_function.cron.arn,
      aws_lambda_function.stats.arn,
    ]
  }
}

resource "aws_iam_user_policy" "lambda_deploy" {
  name   = "lambda-deploy"
  user   = aws_iam_user.github_ci.name
  policy = data.aws_iam_policy_document.lambda_deploy.json
}
