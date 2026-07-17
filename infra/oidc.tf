# GitHub Actions OIDC — lets this repo's workflows assume an AWS role with
# short-lived tokens instead of long-lived access keys.
#
# The account-wide OIDC provider for token.actions.githubusercontent.com is
# already managed by VuDrochkaBot's Terraform (one provider per URL per
# account), so it is referenced here as a data source, not created.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# Trust policy: only this repo's deploy branches may assume the role.
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for b in var.deploy_ref_branches : "repo:${var.github_repo}:ref:refs/heads/${b}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "github-actions-chatcheck-deploy"
  description          = "GitHub Actions OIDC role: push the chatcheck-bot image to ECR and roll the Lambdas"
  max_session_duration = 3600
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
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

resource "aws_iam_role_policy" "ecr_push" {
  name   = "ecr-push"
  role   = aws_iam_role.deploy.id
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
    ]
  }
}

resource "aws_iam_role_policy" "lambda_deploy" {
  name   = "lambda-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.lambda_deploy.json
}
